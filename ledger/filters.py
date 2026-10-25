"""The filters of the transaction list and of the day totals (ADR 0003).

The query parameters are validated by one serializer, so a bad value is a ``400`` on its own parameter with a stable
code, and the schema describes them from the same class. ``category_id`` and ``account_id`` repeat
(``?category_id=a&category_id=b``). An id that matches nothing is not an error: the page is empty. A parameter the
list does not know is ignored.
"""

import uuid
from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.request import Request

from common.serializers import TextField
from config.error_codes import ErrorCode

from .models import Transaction, TransactionKind

MAX_IDS = 20
SEARCH_MIN = 2
SEARCH_MAX = 100


@extend_schema_field(
    {
        "type": "array",
        "items": {"type": "string", "format": "uuid"},
        "maxItems": MAX_IDS,
    }
)
class IdListField(serializers.Field):  # type: ignore[misc]
    """A repeatable id filter: at most 20 UUIDs. More is ``too_many_values``; a value that is no UUID is ``invalid``."""

    default_error_messages: ClassVar[dict[str, str]] = {
        "invalid": "A list of ids, each a UUID.",
        ErrorCode.TOO_MANY_VALUES.value: f"At most {MAX_IDS} values.",
    }

    def to_internal_value(self, data: Any) -> list[uuid.UUID]:
        values = data if isinstance(data, list) else [data]
        if len(values) > MAX_IDS:
            self.fail(ErrorCode.TOO_MANY_VALUES.value)
        ids: list[uuid.UUID] = []
        for value in values:
            try:
                ids.append(uuid.UUID(str(value)))
            except ValueError:
                self.fail("invalid")
        return ids

    def to_representation(self, value: list[uuid.UUID]) -> list[str]:
        return [str(item) for item in value]


class DayFilterSerializer(serializers.Serializer):  # type: ignore[misc]
    """The filters the day totals take: the same as the list, except ``search``."""

    date_from = serializers.DateField(
        required=False, help_text="The first day, inclusive (`YYYY-MM-DD`)."
    )
    date_to = serializers.DateField(
        required=False, help_text="The last day, inclusive (`YYYY-MM-DD`)."
    )
    category_id = IdListField(
        required=False,
        help_text="Repeat to match any of up to 20 categories. An id that matches nothing is not an error.",
    )
    account_id = IdListField(
        required=False,
        help_text="Repeat to match any of up to 20 accounts. An id that matches nothing is not an error.",
    )
    kind = serializers.ChoiceField(choices=TransactionKind.choices, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        first, last = attrs.get("date_from"), attrs.get("date_to")
        if first is not None and last is not None and first > last:
            raise ValidationError(
                {
                    "date_to": [
                        ErrorDetail(
                            "The last day is before the first.",
                            code=ErrorCode.INVALID_DATE_RANGE.value,
                        )
                    ]
                }
            )
        return attrs


class TransactionFilterSerializer(DayFilterSerializer):
    """The filters of the transaction list: the day filters and ``search`` on the note."""

    search = TextField(
        required=False,
        min_length=SEARCH_MIN,
        max_length=SEARCH_MAX,
        help_text=(
            "A case-insensitive substring of the note, 2 to 100 characters; `%` and `_` are ordinary characters. A "
            "list asked with `search` counts in the search rate group."
        ),
    )


_REPEATED = ("category_id", "account_id")


def parse(
    serializer_class: type[DayFilterSerializer], request: Request
) -> dict[str, Any]:
    """The validated filters of a request (``400`` on the parameter that is wrong)."""
    data: dict[str, Any] = {}
    for name in serializer_class().fields:
        if name in _REPEATED:
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
    queryset: QuerySet[Transaction], filters: dict[str, Any]
) -> QuerySet[Transaction]:
    """Narrow ``queryset`` by the validated ``filters``."""
    if "date_from" in filters:
        queryset = queryset.filter(date__gte=filters["date_from"])
    if "date_to" in filters:
        queryset = queryset.filter(date__lte=filters["date_to"])
    if "category_id" in filters:
        queryset = queryset.filter(category_id__in=filters["category_id"])
    if "account_id" in filters:
        queryset = queryset.filter(account_id__in=filters["account_id"])
    if "kind" in filters:
        queryset = queryset.filter(kind=filters["kind"])
    if "search" in filters:
        queryset = queryset.filter(
            note__icontains=filters["search"]
        )  # Django escapes % and _
    return queryset
