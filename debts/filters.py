"""The filters of the debt list and the ``today`` of a read.

One serializer validates the query parameters, so a bad value is a ``400`` on its own parameter with a stable code and
the schema describes them from the same class. ``status`` repeats (``?status=open&status=overdue``). A parameter the
list does not know is ignored.
"""

import datetime
from functools import reduce
from operator import or_
from typing import Any, ClassVar

from django.db.models import (
    BigIntegerField,
    F,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.request import Request

from common.days import day_in_range
from common.serializers import TextField

from .models import Debt, Direction, Repayment, Status

SEARCH_MIN = 2
SEARCH_MAX = 50

TODAY_HELP = (
    "A calendar day (`YYYY-MM-DD`) that replaces the person's current day when the `status` is worked out. The app "
    "leaves it out."
)


class TodaySerializer(serializers.Serializer):  # type: ignore[misc]
    today = serializers.DateField(
        required=False, validators=[day_in_range], help_text=TODAY_HELP
    )


@extend_schema_field(
    {
        "type": "array",
        "items": {"type": "string", "enum": [name for name, _ in Status.choices]},
        "maxItems": len(Status.choices),
    }
)
class StatusListField(serializers.Field):  # type: ignore[misc]
    """A repeatable ``status``: each value one of the three statuses; anything else is ``invalid_choice`` on ``status``."""

    default_error_messages: ClassVar[dict[str, str]] = {
        "invalid_choice": "Each status is `open`, `overdue` or `settled`."
    }

    def to_internal_value(self, data: Any) -> list[str]:
        values = data if isinstance(data, list) else [data]
        if len(values) > len(Status.choices) or any(
            value not in Status.values for value in values
        ):
            self.fail("invalid_choice")
        return [str(value) for value in values]

    def to_representation(self, value: list[str]) -> list[str]:
        return list(value)


class DebtFilterSerializer(TodaySerializer):
    direction = serializers.ChoiceField(choices=Direction.choices, required=False)
    status = StatusListField(
        required=False, help_text="Repeat to match any of the statuses."
    )
    search = TextField(
        required=False,
        min_length=SEARCH_MIN,
        max_length=SEARCH_MAX,
        help_text=(
            "A case-insensitive substring of the person, 2 to 50 characters; `%` and `_` are ordinary characters. "
            "A list asked with `search` counts in the search rate group."
        ),
    )


def parse(serializer_class: type[TodaySerializer], request: Request) -> dict[str, Any]:
    """The validated parameters of a request (``400`` on the parameter that is wrong)."""
    data: dict[str, Any] = {}
    for name, field in serializer_class().fields.items():
        if isinstance(field, StatusListField):
            values = request.query_params.getlist(name)
            if values:
                data[name] = values
        elif name in request.query_params:
            data[name] = request.query_params[name]
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    parsed: dict[str, Any] = serializer.validated_data
    return parsed


def apply(
    queryset: QuerySet[Debt], filters: dict[str, Any], today: datetime.date
) -> QuerySet[Debt]:
    """Narrow ``queryset`` by the validated ``filters``; ``today`` is the day an overdue debt is counted from."""
    if "direction" in filters:
        queryset = queryset.filter(direction=filters["direction"])
    if "search" in filters:
        queryset = queryset.filter(
            person__icontains=filters["search"]
        )  # Django escapes % and _
    if "status" in filters:
        repaid = (
            Repayment.objects.filter(debt=OuterRef("pk"))
            .values("debt")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        queryset = queryset.annotate(
            repaid_total=Coalesce(
                Subquery(repaid, output_field=BigIntegerField()),
                Value(0),
                output_field=BigIntegerField(),
            )
        )
        settled = Q(repaid_total__gte=F("amount"))
        overdue = ~settled & Q(due_date__lt=today)
        by_status = {
            Status.SETTLED: settled,
            Status.OVERDUE: overdue,
            Status.OPEN: ~settled & ~overdue,
        }
        queryset = queryset.filter(
            reduce(or_, (by_status[Status(name)] for name in filters["status"]))
        )
    return queryset
