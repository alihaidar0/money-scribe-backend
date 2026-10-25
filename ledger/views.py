from typing import Any

from django.db.models import Q, QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.serializers import BaseSerializer

from config.api import IdempotentCreateMixin, OwnerScopedMixin, VersionedMixin
from config.api_errors import (
    CONFLICT_RESPONSE,
    ETAG_HEADER,
    IDEMPOTENCY_KEY_HEADER,
    IDEMPOTENT_REPLAYED_HEADER,
    IF_MATCH_HEADER,
    PRECONDITION_FAILED_RESPONSE,
    UNPROCESSABLE_RESPONSE,
)

from . import filters, services
from .models import Account, Category, Transaction
from .permissions import CustomCategoriesOnly
from .serializers import (
    AccountInputSerializer,
    AccountSerializer,
    CategoryInputSerializer,
    CategorySerializer,
    TransactionCreateSerializer,
    TransactionSerializer,
    TransactionUpdateSerializer,
)

ACCOUNTS = ["accounts"]
CATEGORIES = ["categories"]


# --- accounts ---------------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="account_list",
        tags=ACCOUNTS,
        summary="The person's accounts",
        description=(
            'The accounts in the order they were made (the default "Card" before "Cash"). Cursor pagination '
            "(`limit`, `cursor`; follow `next`): ask `limit=100` to get the whole list in one call. A person who "
            "deleted every account gets `200` with an empty `results`."
        ),
        responses={200: AccountSerializer},
    ),
    post=extend_schema(
        operation_id="account_create",
        tags=ACCOUNTS,
        summary="Add an account",
        description=(
            "Needs an `Idempotency-Key`. A person has at most 20 accounts (`409` `account_limit_reached`). The name "
            'is unique per person in any case (`400` `name_taken`, `attr` `name`). Every account gets "Card" and '
            '"Cash" when its email is verified, in the language of the account; they are ordinary accounts.'
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER, IDEMPOTENT_REPLAYED_HEADER, ETAG_HEADER],
        request=AccountInputSerializer,
        responses={
            201: AccountSerializer,
            409: CONFLICT_RESPONSE,
            422: UNPROCESSABLE_RESPONSE,
        },
    ),
)
class AccountList(OwnerScopedMixin, IdempotentCreateMixin, ListCreateAPIView):  # type: ignore[misc]
    queryset = Account.objects.all()
    cursor_ordering = ("created_at", "id")

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        method = getattr(self.request, "method", "GET")
        return AccountInputSerializer if method == "POST" else AccountSerializer

    def perform_create(self, serializer: Any) -> None:
        serializer.instance = services.create_account(
            owner=self.request.user, data=serializer.validated_data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="account_read",
        tags=ACCOUNTS,
        summary="Read an account",
        description="The account, or `404` when it is not the person's. Answers the `ETag` of its `version`.",
        parameters=[ETAG_HEADER],
        responses={200: AccountSerializer},
    ),
    patch=extend_schema(
        operation_id="account_update",
        tags=ACCOUNTS,
        summary="Change an account",
        description=(
            "Changes `name` and `kind`. Send `If-Match` with the `version`: a stale one is `412` and changes nothing. "
            "A name that is taken is `400` `name_taken`. Any other field is `400` `read_only`."
        ),
        parameters=[IF_MATCH_HEADER, ETAG_HEADER],
        request=AccountInputSerializer,
        responses={200: AccountSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
    delete=extend_schema(
        operation_id="account_delete",
        tags=ACCOUNTS,
        summary="Delete an account",
        description=(
            "Deletes the account (`204`); `If-Match` is checked as for a change. An account that has transactions "
            "is refused and nothing changes: `409` `account_has_transactions`. A repeated delete is `404`: treat it "
            "as done."
        ),
        parameters=[IF_MATCH_HEADER],
        responses={
            204: None,
            409: CONFLICT_RESPONSE,
            412: PRECONDITION_FAILED_RESPONSE,
        },
    ),
)
class AccountDetail(OwnerScopedMixin, VersionedMixin, RetrieveUpdateDestroyAPIView):  # type: ignore[misc]
    queryset = Account.objects.all()
    serializer_class = AccountInputSerializer
    http_method_names = ("get", "patch", "delete", "options")

    def perform_update(self, serializer: Any) -> None:
        serializer.instance = services.update_account(
            account=serializer.instance, data=serializer.validated_data
        )

    def perform_destroy(self, instance: Account) -> None:
        services.delete_account(instance)


# --- categories -------------------------------------------------------------------------------


class VisibleCategoriesMixin:
    """The built-in categories, which everyone sees, and the person's own: nobody else's."""

    queryset = Category.objects.all()

    def get_queryset(self) -> QuerySet[Category]:
        queryset: QuerySet[Category] = super().get_queryset()  # type: ignore[misc]
        return queryset.filter(Q(owner__isnull=True) | Q(owner=self.request.user))  # type: ignore[attr-defined]


@extend_schema_view(
    get=extend_schema(
        operation_id="category_list",
        tags=CATEGORIES,
        summary="The categories a person can use",
        description=(
            "The seven built-in categories first, in a fixed order, then the person's own in the order they were "
            "made. Cursor pagination (`limit`, `cursor`; follow `next`): ask `limit=100` to get the whole list in "
            "one call."
        ),
        responses={200: CategorySerializer},
    ),
    post=extend_schema(
        operation_id="category_create",
        tags=CATEGORIES,
        summary="Add a custom category",
        description=(
            "Needs an `Idempotency-Key`. A custom category is always of the kind `both` and has no `key`; a `kind` "
            "in the request is `400` `read_only`. The `icon` is one of the eight the picker offers (`400` "
            "`invalid_choice`). The name is unique among the person's custom categories in any case and is not a "
            "built-in key or English name (`400` `name_taken`). A person has at most 50 custom categories (`409` "
            "`category_limit_reached`); the built-in ones do not count."
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER, IDEMPOTENT_REPLAYED_HEADER, ETAG_HEADER],
        request=CategoryInputSerializer,
        responses={
            201: CategorySerializer,
            409: CONFLICT_RESPONSE,
            422: UNPROCESSABLE_RESPONSE,
        },
    ),
)
class CategoryList(VisibleCategoriesMixin, IdempotentCreateMixin, ListCreateAPIView):  # type: ignore[misc]
    cursor_ordering = ("position", "created_at", "id")

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        method = getattr(self.request, "method", "GET")
        return CategoryInputSerializer if method == "POST" else CategorySerializer

    def perform_create(self, serializer: Any) -> None:
        serializer.instance = services.create_category(
            owner=self.request.user, data=serializer.validated_data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="category_read",
        tags=CATEGORIES,
        summary="Read a category",
        description="A built-in category or one of the person's own, or `404`. Answers the `ETag` of its `version`.",
        parameters=[ETAG_HEADER],
        responses={200: CategorySerializer},
    ),
    patch=extend_schema(
        operation_id="category_update",
        tags=CATEGORIES,
        summary="Change a custom category",
        description=(
            "Changes `name` and `icon` of a custom category; `If-Match` is checked as for an account. A built-in "
            "category cannot be changed: `403` `category_is_builtin`. Any other field, `kind` included, is `400` "
            "`read_only`."
        ),
        parameters=[IF_MATCH_HEADER, ETAG_HEADER],
        request=CategoryInputSerializer,
        responses={200: CategorySerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
    delete=extend_schema(
        operation_id="category_delete",
        tags=CATEGORIES,
        summary="Delete a custom category",
        description=(
            "Deletes a custom category (`204`); its transactions move to the built-in `other` in the same database "
            "transaction (ADR 0017). `If-Match` is checked as for an account. A built-in category cannot be "
            "deleted: `403` `category_is_builtin`. A repeated delete is `404`: treat it as done."
        ),
        parameters=[IF_MATCH_HEADER],
        responses={204: None, 412: PRECONDITION_FAILED_RESPONSE},
    ),
)
class CategoryDetail(
    VersionedMixin,
    VisibleCategoriesMixin,
    RetrieveUpdateDestroyAPIView,  # type: ignore[misc]
):
    serializer_class = CategoryInputSerializer
    permission_classes = (IsAuthenticated, CustomCategoriesOnly)
    http_method_names = ("get", "patch", "delete", "options")

    def perform_update(self, serializer: Any) -> None:
        serializer.instance = services.update_category(
            category=serializer.instance, data=serializer.validated_data
        )

    def perform_destroy(self, instance: Category) -> None:
        services.delete_category(instance)


# --- transactions -----------------------------------------------------------------------------

TRANSACTIONS = ["transactions"]


@extend_schema_view(
    get=extend_schema(
        operation_id="transaction_list",
        tags=TRANSACTIONS,
        summary="The person's transactions",
        description=(
            "Newest day first, then the entry added last first (`-date`, `-created_at`, `-id`). Cursor pagination "
            "(`limit` 50 by default, 100 at most; follow `next`). The filters narrow the list; the same ones (except "
            "`search`) narrow the day totals. `search` is a case-insensitive substring of the note, 2 to 100 "
            "characters. An id that matches nothing, or no match at all, is `200` with an empty `results`."
        ),
        parameters=[filters.TransactionFilterSerializer],
        responses={200: TransactionSerializer},
    ),
    post=extend_schema(
        operation_id="transaction_create",
        tags=TRANSACTIONS,
        summary="Add a transaction",
        description=(
            "Needs an `Idempotency-Key`. `amount` is a decimal string with exactly the digits of `currency` and "
            "greater than 0; the `kind` carries the sign. The `date` is a calendar day from 2000-01-01 to 2099-12-31 "
            "(`400` `date_out_of_range`). The category must be a built-in one or the person's own and must allow the "
            "`kind` (`400` `category_not_allowed` on `category_id`); an unknown id, or one that belongs to someone "
            "else, is `400` `does_not_exist` on its field, with the same answer for both. A person has at most "
            "100 000 transactions (`409` `transaction_limit_reached`)."
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER, IDEMPOTENT_REPLAYED_HEADER, ETAG_HEADER],
        request=TransactionCreateSerializer,
        responses={
            201: TransactionSerializer,
            409: CONFLICT_RESPONSE,
            422: UNPROCESSABLE_RESPONSE,
        },
    ),
)
class TransactionList(OwnerScopedMixin, IdempotentCreateMixin, ListCreateAPIView):  # type: ignore[misc]
    queryset = Transaction.objects.all()
    cursor_ordering = ("-date", "-created_at", "-id")

    def get_queryset(self) -> QuerySet[Transaction]:
        queryset: QuerySet[Transaction] = super().get_queryset()
        if self.request.method != "GET":
            return queryset
        parsed = filters.parse(filters.TransactionFilterSerializer, self.request)
        return filters.apply(queryset, parsed)

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        method = getattr(self.request, "method", "GET")
        return (
            TransactionCreateSerializer if method == "POST" else TransactionSerializer
        )

    def perform_create(self, serializer: Any) -> None:
        serializer.instance = services.create_transaction(
            owner=self.request.user, data=serializer.validated_data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="transaction_read",
        tags=TRANSACTIONS,
        summary="Read a transaction",
        description="The transaction, or `404` when it is not the person's. Answers the `ETag` of its `version`.",
        parameters=[ETAG_HEADER],
        responses={200: TransactionSerializer},
    ),
    patch=extend_schema(
        operation_id="transaction_update",
        tags=TRANSACTIONS,
        summary="Change a transaction",
        description=(
            "Changes any of `kind`, `amount`, `date`, `category_id`, `account_id`, `note`; the rules are those of the "
            "create, and a change of the kind or the category checks the category again. The `currency` cannot be "
            "changed: sending it, even the stored one, is `400` `read_only`, like any other field that cannot be "
            "written. Send `If-Match` with the `version`: a stale one (a category delete that moved this transaction "
            "counts) is `412` and changes nothing."
        ),
        parameters=[IF_MATCH_HEADER, ETAG_HEADER],
        request=TransactionUpdateSerializer,
        responses={200: TransactionSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
    delete=extend_schema(
        operation_id="transaction_delete",
        tags=TRANSACTIONS,
        summary="Delete a transaction",
        description=(
            "Deletes the transaction for good (`204`); `If-Match` is checked as for a change. A repeated delete is "
            "`404`: treat it as done."
        ),
        parameters=[IF_MATCH_HEADER],
        responses={204: None, 412: PRECONDITION_FAILED_RESPONSE},
    ),
)
class TransactionDetail(OwnerScopedMixin, VersionedMixin, RetrieveUpdateDestroyAPIView):  # type: ignore[misc]
    queryset = Transaction.objects.all()
    serializer_class = TransactionUpdateSerializer
    http_method_names = ("get", "patch", "delete", "options")

    def perform_update(self, serializer: Any) -> None:
        serializer.instance = services.update_transaction(
            record=serializer.instance, data=serializer.validated_data
        )
