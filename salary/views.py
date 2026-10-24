import datetime
from typing import Any

from django.db import IntegrityError, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    ListAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

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
from config.preconditions import (
    check_create_only,
    check_if_match,
    check_if_match_on_create,
    with_etag,
)
from idempotency import services as idempotency

from . import services
from .models import FixedItem, SalaryMonth
from .serializers import (
    CopyFixedItemsSerializer,
    DefaultsSerializer,
    FixedItemInputSerializer,
    FixedItemSerializer,
    SalaryMonthInputSerializer,
    SalaryMonthSerializer,
)

TAG = ["salary"]
MONTH = OpenApiParameter(
    name="month",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="The month, `YYYY-MM`, from 2000-01 to 2099-12. Any other key is not a route (a plain `404`).",
)


def _month_response(
    month: SalaryMonth, status_code: int = status.HTTP_200_OK
) -> Response:
    """The month with its ``ETag``; ``400 invalid_amount`` if its sums pass the largest amount."""
    return with_etag(Response(SalaryMonthSerializer(month).data, status=status_code))


@extend_schema_view(
    get=extend_schema(
        operation_id="salary_month_list",
        tags=TAG,
        summary="The history of salary months",
        description=(
            "The user's saved months, newest first, each with its results, fixed items, totals and balances, so the "
            "app needs one call to show the history and to compare two months. Cursor pagination (`limit`, `cursor`; "
            "follow `next`). An empty history is `200` with an empty `results`."
        ),
    )
)
class SalaryMonthList(OwnerScopedMixin, ListAPIView):  # type: ignore[misc]
    serializer_class = SalaryMonthSerializer
    cursor_ordering = ("-month", "-id")

    queryset = SalaryMonth.objects.prefetch_related("fixed_items")


class SalaryMonthDetail(APIView):  # type: ignore[misc]
    """One month by its key: read, create or replace, delete."""

    @extend_schema(
        operation_id="salary_month_read",
        tags=TAG,
        summary="Read a salary month",
        description="The month, or `404` when it is not saved. Answers the `ETag` of its `version`.",
        parameters=[MONTH, ETAG_HEADER],
        responses={200: SalaryMonthSerializer},
    )
    def get(self, request: Request, month: datetime.date) -> Response:
        return _month_response(services.month_of(request.user, month))

    @extend_schema(
        operation_id="salary_month_put",
        tags=TAG,
        summary="Save a salary month",
        description=(
            "Creates the month (`201`) or replaces its inputs as a whole (`200`); the server calculates the results. "
            "The fixed items are never sent here and are never touched. Send `If-None-Match: *` when the form is "
            "for a month the app did not find, so a month created meanwhile on another device is not replaced "
            "(`412`), or `If-Match` with the `version` of the month it loaded. Errors: `400` on the field that is "
            "wrong (`invalid_amount` for an amount that does not fit the currency or a result above the largest "
            "amount, `invalid_choice` for a currency); `409` `month_has_fixed_items` for another currency while the "
            "month has fixed items; `412` `precondition_failed`."
        ),
        parameters=[MONTH, IF_MATCH_HEADER, ETAG_HEADER],
        request=SalaryMonthInputSerializer,
        responses={
            200: SalaryMonthSerializer,
            201: SalaryMonthSerializer,
            409: CONFLICT_RESPONSE,
            412: PRECONDITION_FAILED_RESPONSE,
        },
    )
    def put(self, request: Request, month: datetime.date) -> Response:
        serializer = SalaryMonthInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        for attempt in (1, 2):
            try:
                return self._save(request, month, data)
            except IntegrityError:
                # Two requests created the same month at once; the second finds it on the next try.
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    def _save(
        self, request: Request, month: datetime.date, data: dict[str, Any]
    ) -> Response:
        with transaction.atomic():
            existing = (
                SalaryMonth.objects.select_for_update()
                .filter(owner=request.user, month=month)
                .first()
            )
            check_create_only(request, existing is not None)
            if existing is None:
                check_if_match_on_create(request)
            else:
                check_if_match(request, existing.version)
            saved = services.save_month(
                owner=request.user, month=month, data=data, existing=existing
            )
            return _month_response(
                saved,
                status.HTTP_201_CREATED if existing is None else status.HTTP_200_OK,
            )

    @extend_schema(
        operation_id="salary_month_delete",
        tags=TAG,
        summary="Delete a salary month",
        description=(
            "Deletes the month and its fixed items (`204`). `404` when it is not saved, `412` for a stale `If-Match`."
        ),
        parameters=[MONTH, IF_MATCH_HEADER],
        responses={204: None, 412: PRECONDITION_FAILED_RESPONSE},
    )
    def delete(self, request: Request, month: datetime.date) -> Response:
        with transaction.atomic():
            found = services.lock_month(request.user, month)
            check_if_match(request, found.version)
            found.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SalaryMonthDefaults(APIView):  # type: ignore[misc]
    @extend_schema(
        operation_id="salary_month_defaults",
        tags=TAG,
        summary="The inputs to pre-fill a month",
        description=(
            "The inputs of the form for a month, whether or not it is saved. Carried from the most recent saved month "
            "(never the month itself): `working_hours_in_month`, `tax_rate_basis_points`, and `base_salary` and "
            "`food_per_day` only when the currency is the same (else `null`). The pay schedule is that month's, then "
            "the app's own defaults (one payment, days 20 and 5; tax 1300). `working_days_in_month` and "
            "`working_days_first_half` are `null` and `extra_hours_hundredths` is `0`."
        ),
        parameters=[MONTH],
        responses={200: DefaultsSerializer},
    )
    def get(self, request: Request, month: datetime.date) -> Response:
        return Response(
            DefaultsSerializer(services.defaults_for(request.user, month)).data
        )


# --- fixed items ------------------------------------------------------------------------------


class _AddFixedItem(CreateAPIView):  # type: ignore[misc]
    """The create itself; ``FixedItemCreate`` makes it safe to repeat."""

    serializer_class = FixedItemInputSerializer

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        month: datetime.date = kwargs["month"]
        found = services.month_of(
            request.user, month
        )  # a 404 before the month is locked
        serializer = FixedItemInputSerializer(
            data=request.data, context={"currency": found.currency}
        )
        serializer.is_valid(raise_exception=True)
        locked = services.lock_month(request.user, month)
        item = services.add_item(month=locked, data=serializer.validated_data)
        services.checked(locked)  # the sums must stay within the largest amount
        return with_etag(
            Response(serializer.to_representation(item), status=status.HTTP_201_CREATED)
        )


@extend_schema_view(
    post=extend_schema(
        operation_id="salary_fixed_item_create",
        tags=TAG,
        summary="Add a fixed item to a month",
        description=(
            "Adds a fixed income or expense to a saved month (`404` when the month is not saved). Needs an "
            "`Idempotency-Key`. The item has no currency of its own; one that is sent must be the month's "
            "(`400` `currency_mismatch`). A month holds at most 100 items (`409` `fixed_item_limit_reached`). The "
            "month's `fixed_totals` and `balances` change, so the app reads the month again."
        ),
        parameters=[
            MONTH,
            IDEMPOTENCY_KEY_HEADER,
            IDEMPOTENT_REPLAYED_HEADER,
            ETAG_HEADER,
        ],
        request=FixedItemInputSerializer,
        responses={
            201: FixedItemSerializer,
            409: CONFLICT_RESPONSE,
            422: UNPROCESSABLE_RESPONSE,
        },
    )
)
class FixedItemCreate(IdempotentCreateMixin, _AddFixedItem):
    pass


class FixedItemsCopy(APIView):  # type: ignore[misc]
    @extend_schema(
        operation_id="salary_fixed_items_copy",
        tags=TAG,
        summary="Copy the fixed items of another month",
        description=(
            "Adds the fixed items of another saved month to this one (`name`, `amount`, `kind`, `part`, with new ids) "
            "and answers this month. Needs an `Idempotency-Key`. `from` is the month to copy from; without it, the "
            "most recent saved month before this one. Errors: `404` for a month or a source that is not saved (also "
            "when there is no earlier month); `400` on `from` when it is the month itself or has another currency "
            "(`currency_mismatch`); `409` `fixed_item_limit_reached` with nothing copied when the total would pass "
            "100. A source with no items copies nothing and is not an error."
        ),
        parameters=[MONTH, IDEMPOTENCY_KEY_HEADER, IDEMPOTENT_REPLAYED_HEADER],
        request=CopyFixedItemsSerializer,
        responses={
            200: SalaryMonthSerializer,
            409: CONFLICT_RESPONSE,
            422: UNPROCESSABLE_RESPONSE,
        },
    )
    def post(self, request: Request, month: datetime.date) -> Response:
        serializer = CopyFixedItemsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data.get("from")
        source = datetime.date(int(raw[:4]), int(raw[5:7]), 1) if raw else None

        def action() -> Response:
            target = services.copy_items(owner=request.user, month=month, source=source)
            return _month_response(target)

        answer: Response = idempotency.run(request, action)
        return answer


@extend_schema_view(
    patch=extend_schema(
        operation_id="salary_fixed_item_update",
        tags=TAG,
        summary="Change a fixed item",
        description=(
            "Changes any of `name`, `amount`, `kind`, `part`; the `currency`, if sent, must be the month's. Send "
            "`If-Match` with the item's `version`: a stale one is `412` and changes nothing. Answers the item and "
            "its new `ETag`; the month's `version` does not change, but its `fixed_totals` and `balances` do."
        ),
        parameters=[IF_MATCH_HEADER, ETAG_HEADER],
        request=FixedItemInputSerializer,
        responses={200: FixedItemSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
    delete=extend_schema(
        operation_id="salary_fixed_item_delete",
        tags=TAG,
        summary="Delete a fixed item",
        description=(
            "Deletes the item (`204`); `If-Match` is checked as for a change. A repeated delete is `404`: treat it "
            "as done."
        ),
        parameters=[IF_MATCH_HEADER],
        responses={204: None, 412: PRECONDITION_FAILED_RESPONSE},
    ),
)
class FixedItemDetail(OwnerScopedMixin, VersionedMixin, RetrieveUpdateDestroyAPIView):  # type: ignore[misc]
    owner_field = "salary_month__owner"
    queryset = FixedItem.objects.select_related("salary_month")
    serializer_class = FixedItemInputSerializer
    http_method_names = ("patch", "delete", "options")

    def perform_update(self, serializer: Any) -> None:
        item = services.update_item(
            item=serializer.instance, data=serializer.validated_data
        )
        services.checked(item.salary_month)
