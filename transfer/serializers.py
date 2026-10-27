from typing import Any, ClassVar

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, ValidationError

from common.days import day_in_range
from common.serializers import StrictBooleanField
from config.error_codes import ErrorCode
from users.serializers import PasswordField

from .export import KINDS
from .importing import DATASET_CHOICES, MAX_ROWS


@extend_schema_field(
    {
        "type": "array",
        "items": {"type": "string", "enum": list(KINDS)},
        "minItems": 1,
        "maxItems": len(KINDS),
        "uniqueItems": True,
    }
)
class KindsField(serializers.Field):  # type: ignore[misc]
    """One to five different kinds of data. Empty is ``min_length``, more than five ``max_length``; an unknown kind or
    a repeated one is ``invalid_choice``."""

    default_error_messages: ClassVar[dict[str, str]] = {
        "invalid": "A list of kinds.",
        "min_length": "At least one kind.",
        "max_length": f"At most {len(KINDS)} kinds.",
        "invalid_choice": "Each kind is `accounts`, `categories`, `transactions`, `debts` or `salary_months`, once.",
    }

    def to_internal_value(self, data: Any) -> list[str]:
        if not isinstance(data, list):
            self.fail("invalid")
        if not data:
            self.fail("min_length")
        if len(data) > len(KINDS):
            self.fail("max_length")
        if any(kind not in KINDS for kind in data) or len(set(data)) != len(data):
            self.fail("invalid_choice")
        return [str(kind) for kind in data]

    def to_representation(self, value: list[str]) -> list[str]:
        return list(value)


class ExportRequestSerializer(serializers.Serializer):  # type: ignore[misc]
    """What to export and the password that allows it. ``from`` is a Python keyword, so it is added in ``get_fields``."""

    kinds = KindsField(
        help_text=(
            "1 to 5 different kinds. One kind is answered as a CSV file, several as a ZIP archive with one file per kind "
            "(always in the order accounts, categories, transactions, debts, salary months)."
        )
    )
    current_password = PasswordField(
        max_length=1024, help_text="The account password; it is never put in a URL."
    )

    def get_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = super().get_fields()
        fields["from"] = serializers.DateField(
            required=False,
            validators=[day_in_range],
            help_text=(
                "The first day, inclusive (`YYYY-MM-DD`). It applies to a transaction's `date`, a debt's `start_date` "
                "and, counted in months, to a salary month's month; accounts and categories have no date, so it does "
                "not apply to them. Both left out means all time."
            ),
        )
        fields["to"] = serializers.DateField(
            required=False,
            validators=[day_in_range],
            help_text="The last day, inclusive (`YYYY-MM-DD`); the same rules as `from`.",
        )
        return fields

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        first, last = attrs.get("from"), attrs.get("to")
        if first is not None and last is not None and first > last:
            raise ValidationError(
                {
                    "to": [
                        ErrorDetail(
                            "The last day is before the first.",
                            code=ErrorCode.INVALID_DATE_RANGE.value,
                        )
                    ]
                }
            )
        return attrs


@extend_schema_field(
    {
        "type": "array",
        "minItems": 1,
        "maxItems": MAX_ROWS,
        "items": {
            "type": "object",
            "required": ["row"],
            "additionalProperties": True,
            "properties": {
                "row": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The line in the file; unique in the call.",
                }
            },
        },
    }
)
class RowsField(serializers.Field):  # type: ignore[misc]
    """1 to 500 rows, each an object with its own positive whole number ``row``, unique in the call."""

    default_error_messages: ClassVar[dict[str, str]] = {
        "invalid": "A list of rows, each with a `row` number that is a positive whole number, once.",
        "min_length": "At least one row.",
        "max_length": f"At most {MAX_ROWS} rows.",
    }

    def to_internal_value(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, list):
            self.fail("invalid")
        if not data:
            self.fail("min_length")
        if len(data) > MAX_ROWS:
            self.fail("max_length")
        seen: set[int] = set()
        for item in data:
            if not isinstance(item, dict):
                self.fail("invalid")
            number = item.get("row")
            if type(number) is not int or number < 1 or number in seen:
                self.fail("invalid")
            seen.add(number)
        return list(data)

    def to_representation(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(value)


class ImportRequestSerializer(serializers.Serializer):  # type: ignore[misc]
    dataset = serializers.ChoiceField(choices=DATASET_CHOICES)
    dry_run = StrictBooleanField(
        required=False,
        default=False,
        help_text=(
            "`true` answers what the call would do and writes nothing, stores nothing and needs no `Idempotency-Key`."
        ),
    )
    rows = RowsField(
        help_text=(
            "Each row has `row` and the fields of the dataset, named like the CSV columns, with the JSON cells as "
            "arrays and an empty cell left out. A field that is not a column is a problem `read_only` on it."
        )
    )


class ImportProblemSerializer(serializers.Serializer):  # type: ignore[misc]
    row = serializers.IntegerField(read_only=True)
    code = serializers.CharField(
        read_only=True,
        help_text=(
            "The code of a single create's rule, or `unknown_category`, `unknown_account`, `duplicate`, `read_only` or a "
            "limit code. A plain string: a client survives a code it does not know."
        ),
    )
    attr = serializers.CharField(
        read_only=True,
        allow_null=True,
        help_text="The field of the row that is wrong (dotted inside `repayments` and `fixed_items`), or `null`.",
    )


class ImportResultSerializer(serializers.Serializer):  # type: ignore[misc]
    dataset = serializers.ChoiceField(choices=DATASET_CHOICES, read_only=True)
    dry_run = serializers.BooleanField(read_only=True)
    imported = serializers.IntegerField(
        read_only=True, help_text="The rows written, or that a dry run would write."
    )
    skipped = serializers.IntegerField(
        read_only=True, help_text="The rows with a problem."
    )
    problems = ImportProblemSerializer(
        many=True,
        read_only=True,
        help_text="One item per skipped row, the first problem of the row, ordered by `row`.",
    )
