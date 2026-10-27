"""The import request as the OpenAPI document shows it: one typed row per dataset, chosen by ``dataset``.

The call itself is validated by ``serializers.ImportRequestSerializer``, which only checks the frame (a known dataset,
1 to 500 rows, each with a unique ``row``), because a row that is wrong must be skipped and reported, never a ``400``
for the whole call. These serializers are never used to validate: they describe the columns of each dataset, named like
the CSV file, so a client can generate a model for each and the contract says exactly what a row holds. The fields come
from the serializers that do validate each row (``rows``), so the document cannot drift from the rules.
"""

from typing import Final

from drf_spectacular.utils import PolymorphicProxySerializer
from rest_framework import serializers

from common.enums import PaymentMode
from common.serializers import (
    AmountField,
    CurrencyField,
    StrictBooleanField,
    WholeNumberField,
)
from salary.models import ItemKind, Part

from . import rows
from .importing import MAX_ROWS

DRY_RUN_HELP: Final = "`true` answers what the call would do and writes nothing, stores nothing and needs no `Idempotency-Key`."
ROWS_HELP: Final = (
    "1 to 500 rows. A field that is not a column of the dataset is a problem `read_only` on it, and an empty cell is "
    "left out."
)


class RowNumber(serializers.Serializer):  # type: ignore[misc]
    """The line number every row carries."""

    row = serializers.IntegerField(
        min_value=1,
        help_text="The line in the file: a positive whole number, unique in the call.",
    )


class ImportRepayment(serializers.Serializer):  # type: ignore[misc]
    """One repayment in the `repayments` cell of a debt."""

    date = serializers.DateField()
    amount = AmountField(allow_zero=False)


class ImportFixedItem(serializers.Serializer):  # type: ignore[misc]
    """One fixed item in the `fixed_items` cell of a salary month. It has no currency: the month's."""

    name = serializers.CharField(max_length=50)
    amount = AmountField(allow_zero=False)
    kind = serializers.ChoiceField(choices=ItemKind.choices)
    part = serializers.ChoiceField(choices=Part.choices, required=False)
    day = serializers.IntegerField(
        min_value=1,
        max_value=31,
        required=False,
        allow_null=True,
        help_text="The day of the month, 1 to 31. Left out, or `null`, for none.",
    )


class AccountImportRow(RowNumber, rows.AccountRow):
    """An account of the file. One whose name exists, in any case, is a `duplicate`."""


class CategoryImportRow(RowNumber, rows.CategoryRow):
    """A custom category of the file. One whose name exists, in any case, is a `duplicate` and keeps its icon."""


class TransactionImportRow(RowNumber, rows.TransactionRow):
    """A transaction of the file. Exactly one of `category_key` (a built-in category) and `category_name` (one of the
    person's own) names its category, and `account_name` an account that exists; none is created."""


class DebtImportRow(RowNumber, rows.DebtRow):
    """A debt of the file, with its repayments as an array."""

    repayments = ImportRepayment(
        many=True,
        required=False,
        help_text="`[]` or a left out cell when there are none. Each is at most the amount still owed.",
    )


class SalaryMonthImportRow(RowNumber, serializers.Serializer):  # type: ignore[misc]
    """A salary month of the file. `extra_hours` and `tax_rate` are the readable forms of the salary month API's
    `extra_hours_hundredths` and `tax_rate_basis_points`."""

    month = serializers.RegexField(
        r"^20[0-9]{2}-(0[1-9]|1[0-2])$", help_text="`YYYY-MM`."
    )
    currency = CurrencyField()
    base_salary = AmountField(allow_zero=False)
    food_per_day = AmountField()
    working_days_in_month = WholeNumberField(min_value=1, max_value=31)
    working_hours_in_month = WholeNumberField(min_value=1)
    extra_hours = serializers.RegexField(
        r"^[0-9]+\.[0-9]{2}$",
        help_text="Hours with exactly two decimals, `0.00` to `744.00` (`7.50` is seven and a half hours).",
    )
    tax_rate = serializers.RegexField(
        r"^[0-9]+\.[0-9]{2}$",
        help_text="A percent with exactly two decimals, `0.00` to `100.00`.",
    )
    payment_mode = serializers.ChoiceField(choices=PaymentMode.choices)
    working_days_first_half = WholeNumberField(
        min_value=0,
        required=False,
        allow_null=True,
        help_text="Required with two payments, up to `working_days_in_month`; ignored with one.",
    )
    first_pay_day = WholeNumberField(min_value=1, max_value=28)
    second_pay_day = WholeNumberField(min_value=1, max_value=28)
    fixed_items = ImportFixedItem(
        many=True,
        required=False,
        help_text="`[]` or a left out cell when there are none.",
    )


def _rows(row: serializers.Serializer) -> serializers.ListField:
    # A list field, not `many=True`: only a field's own length limits reach the document (`minItems`, `maxItems`).
    return serializers.ListField(
        child=row, min_length=1, max_length=MAX_ROWS, help_text=ROWS_HELP
    )


def _dataset(name: str) -> serializers.Field:
    return serializers.ChoiceField(
        choices=[name], help_text=f"The rows are `{name}` rows."
    )


class _Import(serializers.Serializer):  # type: ignore[misc]
    dry_run = StrictBooleanField(required=False, default=False, help_text=DRY_RUN_HELP)


class AccountImport(_Import):
    dataset = _dataset("accounts")
    rows = _rows(AccountImportRow())


class CategoryImport(_Import):
    dataset = _dataset("categories")
    rows = _rows(CategoryImportRow())


class TransactionImport(_Import):
    dataset = _dataset("transactions")
    rows = _rows(TransactionImportRow())


class DebtImport(_Import):
    dataset = _dataset("debts")
    rows = _rows(DebtImportRow())


class SalaryMonthImport(_Import):
    dataset = _dataset("salary_months")
    rows = _rows(SalaryMonthImportRow())


BY_DATASET: Final[dict[str, type[serializers.Serializer]]] = {
    "accounts": AccountImport,
    "categories": CategoryImport,
    "transactions": TransactionImport,
    "debts": DebtImport,
    "salary_months": SalaryMonthImport,
}

# The request body of the import in the document: one shape per dataset, told apart by `dataset`.
IMPORT_REQUEST: Final = PolymorphicProxySerializer(
    component_name="Import",
    serializers=BY_DATASET,
    resource_type_field_name="dataset",
)
