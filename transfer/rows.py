"""The shapes of one imported row, named like the CSV columns (the issue 'CSV export and import').

A row is a JSON object the app made from one line of the file; an empty cell is a missing field and the JSON cells are
arrays. Every row goes through the rules of a single create, so it gets the same codes: these serializers reuse the
ones of the create, and a field that does not exist (an ``id``, say) is ``read_only`` on it.
"""

from typing import Any

from rest_framework import serializers

from common.days import day_in_range
from common.serializers import (
    AmountField,
    CurrencyField,
    MoneySerializerMixin,
    RefuseUnknownFieldsMixin,
    TextField,
)
from debts.serializers import DebtCreateSerializer
from ledger.defaults import CUSTOM_ICONS
from ledger.models import AccountKind, TransactionKind


class AccountRow(RefuseUnknownFieldsMixin, serializers.Serializer):  # type: ignore[misc]
    """An account row: the same two fields as an account create."""

    name = TextField(max_length=30, help_text="1 to 30 characters, trimmed.")
    kind = serializers.ChoiceField(choices=AccountKind.choices)


class CategoryRow(RefuseUnknownFieldsMixin, serializers.Serializer):  # type: ignore[misc]
    """A custom category row. Its kind is always ``both``, so a ``kind`` column is ``read_only``."""

    name = TextField(max_length=30, help_text="1 to 30 characters, trimmed.")
    icon = serializers.ChoiceField(
        choices=[(icon, icon) for icon in CUSTOM_ICONS],
        help_text="One of the icons the picker offers for a custom category. There is no default for a missing one.",
    )


class TransactionRow(
    RefuseUnknownFieldsMixin,
    MoneySerializerMixin,
    serializers.Serializer,  # type: ignore[misc]
):
    """A transaction row. The category is named by exactly one of ``category_key`` and ``category_name``."""

    date = serializers.DateField(validators=[day_in_range])
    kind = serializers.ChoiceField(choices=TransactionKind.choices)
    amount = AmountField(allow_zero=False)
    currency = CurrencyField()
    category_key = TextField(required=False, max_length=20)
    category_name = TextField(required=False, max_length=30)
    account_name = TextField(max_length=30)
    note = TextField(max_length=100, required=False, allow_blank=True, default="")


class DebtRow(DebtCreateSerializer):
    """A debt row: the fields of a debt create and its repayments as an array of ``{date, amount}``."""

    repayments = serializers.ListField(
        child=serializers.DictField(), required=False, max_length=1000
    )


SALARY_COLUMNS = (
    "month",
    "currency",
    "base_salary",
    "food_per_day",
    "working_days_in_month",
    "working_hours_in_month",
    "extra_hours",
    "tax_rate",
    "payment_mode",
    "working_days_first_half",
    "first_pay_day",
    "second_pay_day",
    "fixed_items",
)
FIXED_ITEM_KEYS = ("name", "amount", "kind", "part")

# The salary month's own inputs keep their names and units; the CSV names two of them differently.
RENAMED: dict[str, str] = {
    "extra_hours_hundredths": "extra_hours",
    "tax_rate_basis_points": "tax_rate",
}


def unknown_field(row: dict[str, Any], allowed: tuple[str, ...]) -> str | None:
    """The first field of a row that is not a column of the dataset."""
    return next((name for name in row if name not in allowed), None)
