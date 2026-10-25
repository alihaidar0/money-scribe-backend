import uuid
from typing import Any

from django.db import transaction
from django.db.models import Prefetch, QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    GenericAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
    get_object_or_404,
)
from rest_framework.request import Request
from rest_framework.response import Response
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
from config.preconditions import check_if_match, with_etag

from . import filters, presentation, services
from .models import Debt, Repayment
from .serializers import (
    DebtCreateSerializer,
    DebtSerializer,
    DebtUpdateSerializer,
    RepaymentInputSerializer,
)

TAG = ["debts"]
# The calls that read: the filters and the `today` of a read apply to them (a `HEAD` is a `GET` without a body).
READS = ("GET", "HEAD")
REPAYMENT_ID = OpenApiParameter(
    name="repayment_id",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
    description="The id of a repayment of this debt; another debt's repayment is `404`.",
)


class TodayContextMixin:
    """The person's day for the derived ``status``: the ``today`` parameter of a read, else the person's own day."""

    request: Request

    def parsed_today(self) -> dict[str, Any]:
        """The validated parameters of a read (none for a write: the parameters of a write are ignored)."""
        return {}

    def get_serializer_context(self) -> dict[str, Any]:
        context: dict[str, Any] = super().get_serializer_context()  # type: ignore[misc]
        today = self.parsed_today().get("today") or presentation.today_of(
            self.request.user
        )
        context["today"] = today
        return context


def with_repayments(queryset: QuerySet[Debt]) -> QuerySet[Debt]:
    """Load the repayments of every debt of a page in one more query, in the order the API shows them."""
    return queryset.prefetch_related(
        Prefetch("repayments", queryset=Repayment.objects.order_by("date", "id"))
    )


@extend_schema_view(
    get=extend_schema(
        operation_id="debt_list",
        tags=TAG,
        summary="The person's debts",
        description=(
            "Newest first (`-created_at`, `-id`), each with its `repayments` inline and its derived `repaid`, "
            "`remaining` and `status`. Cursor pagination (`limit` 50 by default, 100 at most; follow `next`). "
            "`status` repeats and matches any of the values; `search` is a case-insensitive substring of the person, "
            "2 to 50 characters. A filter that matches nothing is `200` with an empty `results`."
        ),
        parameters=[filters.DebtFilterSerializer],
        responses={200: DebtSerializer},
    ),
    post=extend_schema(
        operation_id="debt_create",
        tags=TAG,
        summary="Add a debt",
        description=(
            "Needs an `Idempotency-Key`. `amount` is a decimal string with exactly the digits of `currency` and "
            "greater than 0. The dates are calendar days from 2000-01-01 to 2099-12-31 (`400` `date_out_of_range`) "
            "and the `due_date`, if any, is not before the `start_date` (`400` `due_before_start`). The debt starts "
            "with no repayments. A person has at most 500 debts (`409` `debt_limit_reached`)."
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER, IDEMPOTENT_REPLAYED_HEADER, ETAG_HEADER],
        request=DebtCreateSerializer,
        responses={
            201: DebtSerializer,
            409: CONFLICT_RESPONSE,
            422: UNPROCESSABLE_RESPONSE,
        },
    ),
)
class DebtList(
    TodayContextMixin,
    OwnerScopedMixin,
    IdempotentCreateMixin,
    ListCreateAPIView,  # type: ignore[misc]
):
    queryset = Debt.objects.all()
    cursor_ordering = ("-created_at", "-id")

    def parsed_today(self) -> dict[str, Any]:
        if self.request.method not in READS:
            return {}
        if not hasattr(self, "_parsed"):
            self._parsed = filters.parse(filters.DebtFilterSerializer, self.request)
        parsed: dict[str, Any] = self._parsed
        return parsed

    def get_queryset(self) -> QuerySet[Debt]:
        queryset: QuerySet[Debt] = super().get_queryset()
        if self.request.method not in READS:
            return queryset
        context = self.get_serializer_context()
        return with_repayments(
            filters.apply(queryset, self.parsed_today(), context["today"])
        )

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        method = getattr(self.request, "method", "GET")
        return DebtCreateSerializer if method == "POST" else DebtSerializer

    def perform_create(self, serializer: Any) -> None:
        serializer.instance = services.create_debt(
            owner=self.request.user, data=serializer.validated_data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="debt_read",
        tags=TAG,
        summary="Read a debt",
        description="The debt with its repayments, or `404` when it is not the person's. Answers the `ETag` of its `version`.",
        parameters=[filters.TodaySerializer, ETAG_HEADER],
        responses={200: DebtSerializer},
    ),
    patch=extend_schema(
        operation_id="debt_update",
        tags=TAG,
        summary="Change a debt",
        description=(
            "Changes any of `person`, `amount`, `start_date`, `due_date`, `note`; fields not sent keep their value. "
            '`{"due_date": null}` clears the due date and `"note": ""` the note; `note: null` is `400`. The '
            "`direction` and the `currency` cannot be changed: sending them, even the stored value, is `400` "
            "`read_only`, like every derived field and any unknown field. The `amount` cannot go below what was "
            "repaid (`400` `amount_below_repaid`) and the `start_date` cannot go past a repayment (`400` "
            "`start_after_repayment`). Send `If-Match` with the `version`: a stale one is `412` and changes nothing."
        ),
        parameters=[IF_MATCH_HEADER, ETAG_HEADER],
        request=DebtUpdateSerializer,
        responses={200: DebtSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
    delete=extend_schema(
        operation_id="debt_delete",
        tags=TAG,
        summary="Delete a debt",
        description=(
            "Deletes the debt and its repayments in one transaction (`204`); `If-Match` is checked as for a change. "
            "A repeated delete is `404`: treat it as done."
        ),
        parameters=[IF_MATCH_HEADER],
        responses={204: None, 412: PRECONDITION_FAILED_RESPONSE},
    ),
)
class DebtDetail(
    TodayContextMixin,
    OwnerScopedMixin,
    VersionedMixin,
    RetrieveUpdateDestroyAPIView,  # type: ignore[misc]
):
    queryset = Debt.objects.all()
    serializer_class = DebtUpdateSerializer
    http_method_names = ("get", "patch", "delete", "options")

    def parsed_today(self) -> dict[str, Any]:
        if self.request.method not in READS:
            return {}
        if not hasattr(self, "_parsed"):
            self._parsed = filters.parse(filters.TodaySerializer, self.request)
        parsed: dict[str, Any] = self._parsed
        return parsed

    def get_serializer_class(self) -> type[BaseSerializer[Any]]:
        method = getattr(self.request, "method", "GET")
        return DebtSerializer if method == "GET" else DebtUpdateSerializer

    def perform_update(self, serializer: Any) -> None:
        serializer.instance = services.update_debt(
            debt=serializer.instance, data=serializer.validated_data
        )


# --- repayments -------------------------------------------------------------------------------


class _DebtOfTheUrl(TodayContextMixin):
    """The debt in the URL, found among the signed-in person's own (``404`` otherwise), for the repayment calls."""

    queryset = Debt.objects.all()

    def person_debt(self) -> Debt:
        found: Debt = get_object_or_404(
            Debt.objects.filter(owner=self.request.user),
            pk=self.kwargs["pk"],  # type: ignore[attr-defined]
        )
        return found


class _AddRepayment(_DebtOfTheUrl, CreateAPIView):  # type: ignore[misc]
    """The create itself; ``RepaymentCreate`` makes it safe to repeat."""

    serializer_class = RepaymentInputSerializer

    def get_serializer_context(self) -> dict[str, Any]:
        context = super().get_serializer_context()
        context["debt"] = self.person_debt()  # a 404 before anything is locked
        return context

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        debt = services.add_repayment(
            owner=request.user, debt_id=kwargs["pk"], data=serializer.validated_data
        )
        context = {**serializer.context, "debt": debt}
        return Response(
            DebtSerializer(debt, context=context).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    post=extend_schema(
        operation_id="debt_repayment_create",
        tags=TAG,
        summary="Add a repayment",
        description=(
            "Needs an `Idempotency-Key`. Answers `201` with the whole debt, so the app has the new `repaid`, "
            "`remaining` and `status` at once; its `ETag` is the debt's `version`, which a repayment does not change. "
            "The repayment is in the debt's currency: a `currency` in the request is `400` `read_only`. An `amount` "
            "above what remains is `400` `exceeds_remaining` on `amount`, and the error also carries `remaining`. A "
            "`date` before the debt's `start_date` is `400` `repayment_before_start`. A debt has at most 200 "
            "repayments (`409` `repayment_limit_reached`). The debt is locked while the repayment is added, so two "
            "devices cannot overpay it together."
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER, IDEMPOTENT_REPLAYED_HEADER, ETAG_HEADER],
        request=RepaymentInputSerializer,
        responses={
            201: DebtSerializer,
            409: CONFLICT_RESPONSE,
            422: UNPROCESSABLE_RESPONSE,
        },
    )
)
class RepaymentCreate(IdempotentCreateMixin, _AddRepayment):
    pass


@extend_schema_view(
    patch=extend_schema(
        operation_id="debt_repayment_update",
        tags=TAG,
        summary="Change a repayment",
        description=(
            "Changes `amount` and `date`; the rules are those of the create, and the repayment's own previous amount "
            "does not count towards what remains. Answers `200` with the whole debt and the debt's `ETag`. Send "
            "`If-Match` with the **repayment's** `version` (`repayments[].version`): a stale one is `412` and changes "
            "nothing. A repayment of another debt is `404`."
        ),
        parameters=[REPAYMENT_ID, IF_MATCH_HEADER, ETAG_HEADER],
        request=RepaymentInputSerializer,
        responses={200: DebtSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
    delete=extend_schema(
        operation_id="debt_repayment_delete",
        tags=TAG,
        summary="Delete a repayment",
        description=(
            "Deletes the repayment and answers `200` with the whole debt; `If-Match` is the repayment's `version`, "
            "checked as for a change. A repeated delete is `404`: treat it as done."
        ),
        parameters=[REPAYMENT_ID, IF_MATCH_HEADER, ETAG_HEADER],
        responses={200: DebtSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
)
class RepaymentDetail(_DebtOfTheUrl, GenericAPIView):  # type: ignore[misc]
    """Change or delete one repayment, under the lock of its debt (taken first, then the repayment's)."""

    serializer_class = RepaymentInputSerializer
    http_method_names = ("patch", "delete", "options")

    def _locked(
        self, request: Request, pk: uuid.UUID, repayment_id: uuid.UUID
    ) -> tuple[Debt, Repayment]:
        debt = services.lock_debt(request.user, pk)
        repayment = services.lock_repayment(debt, repayment_id)
        check_if_match(request, repayment.version)
        return debt, repayment

    def patch(
        self, request: Request, pk: uuid.UUID, repayment_id: uuid.UUID
    ) -> Response:
        with transaction.atomic():
            debt, repayment = self._locked(request, pk, repayment_id)
            serializer = RepaymentInputSerializer(
                repayment,
                data=request.data,
                partial=True,
                context={**self.get_serializer_context(), "debt": debt},
            )
            serializer.is_valid(raise_exception=True)
            services.update_repayment(
                debt=debt, repayment=repayment, data=serializer.validated_data
            )
        return with_etag(Response(serializer.data))

    def delete(
        self, request: Request, pk: uuid.UUID, repayment_id: uuid.UUID
    ) -> Response:
        with transaction.atomic():
            debt, repayment = self._locked(request, pk, repayment_id)
            repayment.delete()
            context = {**self.get_serializer_context(), "debt": debt}
            body = DebtSerializer(debt, context=context).data
        return with_etag(Response(body))
