import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.db.models import QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.money import AmountOutOfRange, Currency
from common.serializers import CurrencyField
from config.api import OwnerScopedMixin
from config.error_codes import ErrorCode
from config.throttling import ReportsThrottle
from debts import filters as debt_filters
from debts.presentation import today_of
from ledger import filters
from ledger.models import Transaction

from . import debts_report, month_report, overview, services
from .pagination import DayPagination
from .periods import default_currency_of
from .serializers import (
    DayTotalSerializer,
    DebtsReportSerializer,
    MonthReportSerializer,
    OverviewSerializer,
)

TAG = ["reports"]
OVERVIEW_TAG = ["overview"]
MONTH = OpenApiParameter(
    name="month",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="The month, `YYYY-MM`, from 2000-01 to 2099-12. Any other key is not a route (a plain `404`).",
)


class CurrencyQuerySerializer(serializers.Serializer):  # type: ignore[misc]
    currency = CurrencyField(
        required=False,
        help_text=(
            "The currency of every amount in the answer. Left out, it is the Settings default currency (`RUB` until "
            "set). Data in other currencies is not in the figures; `currencies` lists them."
        ),
    )


@contextmanager
def _amounts_in_range() -> Iterator[None]:
    """A sum past the largest amount is ``400 invalid_amount``, never a ``500``."""
    try:
        yield
    except AmountOutOfRange:
        raise ValidationError(
            [
                ErrorDetail(
                    "A total is larger than the largest amount.",
                    code=ErrorCode.INVALID_AMOUNT.value,
                )
            ]
        ) from None


def _currency_of(request: Request) -> Currency:
    parsed = debt_filters.parse(CurrencyQuerySerializer, request)
    chosen = parsed.get("currency")
    return Currency(chosen) if chosen else default_currency_of(request.user)


@extend_schema_view(
    get=extend_schema(
        operation_id="report_day_totals",
        tags=TAG,
        summary="The total of each day",
        description=(
            "One item per day that has transactions, newest day first, each with its currencies inside: `totals` has one "
            "`{currency, total}` per currency, ordered by currency code, where `total` is income minus expenses of that "
            "day in that currency. A day always arrives complete: `limit` (50 by default, 100 at most) counts days, and "
            "`next` follows to the older ones. `date_from` and `date_to` are optional and there is no limit on the span. It "
            "takes the filters of the transaction list except `search`, which is not a parameter of this operation. The "
            "list and the totals read in step: the same order, the same filters. A day with no transaction has no item. A "
            "total past the largest amount is `400` `invalid_amount`."
        ),
        parameters=[filters.DayFilterSerializer],
        responses={200: DayTotalSerializer},
    )
)
class DayTotalsView(OwnerScopedMixin, ListAPIView):  # type: ignore[misc]
    queryset = Transaction.objects.all()
    serializer_class = DayTotalSerializer
    pagination_class = DayPagination
    throttle_classes = (ReportsThrottle,)

    def get_queryset(self) -> QuerySet[Transaction]:
        queryset: QuerySet[Transaction] = super().get_queryset()
        parsed = filters.parse(filters.DayFilterSerializer, self.request)
        return filters.apply(queryset, parsed)

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        queryset = self.get_queryset()
        days = self.paginator.paginate_queryset(queryset, request, view=self)
        try:
            results = services.day_totals(queryset, days)
        except AmountOutOfRange:
            raise ValidationError(
                [
                    ErrorDetail(
                        "A total is larger than the largest amount.",
                        code=ErrorCode.INVALID_AMOUNT.value,
                    )
                ]
            ) from None
        return self.paginator.get_paginated_response(
            DayTotalSerializer(results, many=True).data
        )


@extend_schema_view(
    get=extend_schema(
        operation_id="report_month",
        tags=TAG,
        summary="The month report: spending by category",
        description=(
            "Transactions only, in one `currency`: `income_transactions` is the income transactions dated in the month "
            "(the salary and the recurring income are not included; the Overview has them) and `purchases` is the "
            "expense transactions (the fixed costs are not included). `categories` is the spending by category, the "
            "biggest first, and adds up to `purchases`. A month paid twice also has `first_part` (days 1 to 15) and "
            "`second_part` (day 16 to the end), cut by each transaction's own `date`; a month paid once has `null` "
            "for both. Whether the month has parts follows the saved salary month, or the Settings payment mode for "
            "a month never saved. A month with nothing is `200` with zero totals, no categories and `has_data` false. "
            "Never paginated or cached. An unsupported `currency` is `400` `invalid_choice`."
        ),
        parameters=[MONTH, CurrencyQuerySerializer],
        responses={200: MonthReportSerializer},
    )
)
class MonthReportView(APIView):  # type: ignore[misc]
    throttle_classes = (ReportsThrottle,)

    def get(self, request: Request, month: datetime.date) -> Response:
        currency = _currency_of(request)
        with _amounts_in_range():
            report = month_report.month_report(request.user, month, currency)
            return Response(MonthReportSerializer(report).data)


@extend_schema_view(
    get=extend_schema(
        operation_id="overview_month",
        tags=OVERVIEW_TAG,
        summary="The Overview of a month",
        description=(
            "What came in, what went out and what is left in one `currency`, for the whole month (`total`) and, for a "
            "month paid twice, for each part (`first_part`: days 1 to 15, `second_part`: day 16 to the end; both `null` "
            "for a month paid once). `balance` is `salary` + `recurring_income` + `income_transactions` - `fixed_costs` "
            "- `purchases` + `debt_repayments_received` - `debt_repayments_made`. The salary and the fixed items are "
            "those of the saved month and count only when its currency is `currency`; otherwise those lines are 0 and "
            "`currencies` lists the salary month's currency. Transactions and repayments count by their calendar "
            "`date`; creating a debt is not a movement, a repayment is. `has_data` is true when the month has a saved "
            "salary month or any transaction or repayment, in any currency. A month with nothing is `200` with every "
            "line `0.00`. Never paginated or cached. An unsupported `currency` is `400` `invalid_choice`."
        ),
        parameters=[MONTH, CurrencyQuerySerializer],
        responses={200: OverviewSerializer},
    )
)
class OverviewView(APIView):  # type: ignore[misc]
    throttle_classes = (ReportsThrottle,)

    def get(self, request: Request, month: datetime.date) -> Response:
        currency = _currency_of(request)
        with _amounts_in_range():
            found = overview.overview(request.user, month, currency)
            return Response(OverviewSerializer(found).data)


@extend_schema_view(
    get=extend_schema(
        operation_id="report_debts",
        tags=TAG,
        summary="The debts report",
        description=(
            "`by_currency` has one block per currency that has unsettled debts, ordered by currency code: what the "
            "person is owed (`owed_to_you`, the `remaining` of unsettled debts lent), what they owe (`you_owe`) and "
            "the unsettled debts themselves, `overdue` (by `due_date`, the oldest first) and `open` (by `due_date`, "
            "those with none last). Settled debts are not listed. `has_data` is true when the person has any debt, "
            "settled or not. The `status` is counted in the person's own day; `today` replaces it. Never paginated "
            "(a person has at most 500 debts)."
        ),
        parameters=[debt_filters.TodaySerializer],
        responses={200: DebtsReportSerializer},
    )
)
class DebtsReportView(APIView):  # type: ignore[misc]
    throttle_classes = (ReportsThrottle,)

    def get(self, request: Request) -> Response:
        parsed = debt_filters.parse(debt_filters.TodaySerializer, request)
        today = parsed.get("today") or today_of(request.user)
        return Response(
            DebtsReportSerializer(debts_report.debts_report(request.user, today)).data
        )
