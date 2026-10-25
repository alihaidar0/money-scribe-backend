from typing import Any

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from common.money import AmountOutOfRange
from config.api import OwnerScopedMixin
from config.error_codes import ErrorCode
from config.throttling import ReportsThrottle
from ledger import filters
from ledger.models import Transaction

from . import services
from .pagination import DayPagination
from .serializers import DayTotalSerializer

TAG = ["reports"]


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
