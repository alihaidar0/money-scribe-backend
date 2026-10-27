"""The CSV import: rows of one dataset written one by one, each in its own savepoint (the issue 'CSV export and import').

The caller makes one database transaction of the whole call. A row that passes is written; a row that fails is rolled
back to its savepoint, skipped and reported with its first problem (``row``, ``code``, ``attr``). Only the datasets
``accounts`` and ``categories`` create an account or a category (a transaction row never does), no import reads an id,
and none overwrites: a row equal to an existing record is a
``duplicate``, counted by multiplicity (if the person has 2 identical records and the file 3, one is written), and a
salary month that exists is a ``duplicate`` too. The record caps apply as for a single create. A dry run is the same
work rolled back by the caller, so it answers exactly what a real call would. Nothing here logs a value.
"""

import datetime
import re
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.db.models.functions import Lower
from rest_framework.exceptions import APIException, ErrorDetail, ValidationError

from common.money import Currency, Money
from config.error_codes import ErrorCode
from debts import services as debt_services
from debts.models import Debt
from debts.serializers import RepaymentInputSerializer
from ledger import services as ledger_services
from ledger.models import Account, Category, Transaction
from salary import services as salary_services
from salary.models import SalaryMonth
from salary.serializers import FixedItemInputSerializer, SalaryMonthInputSerializer
from users.models import User

from . import rows as shapes
from .units import InvalidUnit, hours_to_hundredths, percent_to_basis_points

MAX_ROWS = 500
_MONTH = re.compile(r"(20[0-9]{2})-(0[1-9]|1[0-2])")
# The codes only this module sends.
UNKNOWN_CATEGORY = ErrorCode.UNKNOWN_CATEGORY.value
UNKNOWN_ACCOUNT = ErrorCode.UNKNOWN_ACCOUNT.value
DUPLICATE = ErrorCode.DUPLICATE.value


@dataclass(frozen=True)
class Problem:
    row: int
    code: str
    attr: str | None


@dataclass
class ImportResult:
    dataset: str
    imported: int = 0
    problems: list[Problem] = field(default_factory=list)

    @property
    def skipped(self) -> int:
        return len(self.problems)


class RowProblem(Exception):
    """A rule of the import that a row broke: the code and the field of the row it belongs to."""

    def __init__(self, code: str, attr: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.attr = attr


def first_problem(
    detail: Any, path: tuple[str, ...] = ()
) -> tuple[str, str | None] | None:
    """The first error in the detail of a ``ValidationError``: its code and the dotted path of its field."""
    if isinstance(detail, ErrorDetail):
        return detail.code, ".".join(path) or None
    if isinstance(detail, dict):
        for key, value in detail.items():
            found = first_problem(value, (*path, str(key)))
            if found is not None:
                return found
    elif isinstance(detail, list):
        for index, value in enumerate(detail):
            nested = isinstance(value, dict | list)
            found = first_problem(
                value, (*path, str(index)) if nested and path else path
            )
            if found is not None:
                return found
    return None


def problem_of(
    error: Exception, *, prefix: str = "", renames: dict[str, str] | None = None
) -> RowProblem:
    """What a row got wrong, from an error a service or a serializer raised."""
    if isinstance(error, RowProblem):
        code, attr = error.code, error.attr
    elif isinstance(error, ValidationError):
        code, attr = first_problem(error.detail) or ("invalid", None)
    elif isinstance(error, APIException):
        code, attr = str(error.get_codes()), None
    else:
        raise error
    if attr is not None and renames:
        head, _, rest = attr.partition(".")
        attr = ".".join(part for part in (renames.get(head, head), rest) if part)
    if prefix:
        attr = f"{prefix}.{attr}" if attr else prefix
    return RowProblem(code, attr)


class Seen:
    """Counts the existing identical records once per kind of row, so duplicates are counted by multiplicity."""

    def __init__(self) -> None:
        self._left: dict[Hashable, int] = {}

    def duplicate(self, key: Hashable, existing: Callable[[], int]) -> bool:
        if key not in self._left:
            self._left[key] = existing()
        if self._left[key] > 0:
            self._left[key] -= 1
            return True
        return False


# --- accounts and custom categories -----------------------------------------------------------


def _import_account(owner: User, raw: dict[str, Any], seen: Seen) -> None:
    serializer = shapes.AccountRow(data=raw)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    # A name is one account whatever the case, so a repeat is a duplicate (also at the cap, and within the file).
    if ledger_services.account_name_is_taken(owner, data["name"]):
        raise RowProblem(DUPLICATE, "name")
    ledger_services.create_account(owner=owner, data=data)


def _import_category(owner: User, raw: dict[str, Any], seen: Seen) -> None:
    serializer = shapes.CategoryRow(data=raw)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    # Only the person's own categories make a duplicate; a name that is a built-in key or name is `name_taken`, which
    # the create raises, as it does for a single create. An existing category keeps its icon: nothing is overwritten.
    own = (
        Category.objects.filter(owner=owner)
        .annotate(lowered=Lower("name"))
        .filter(lowered=data["name"].lower())
    )
    if own.exists():
        raise RowProblem(DUPLICATE, "name")
    ledger_services.create_category(owner=owner, data=data)


# --- transactions -----------------------------------------------------------------------------


def _category_of(owner: User, data: dict[str, Any]) -> tuple[Category, str]:
    """The category a row names, and the column that named it."""
    key, name = data.get("category_key"), data.get("category_name")
    if key and name:
        raise RowProblem("invalid", "category_name")
    if key:
        found = Category.objects.filter(owner__isnull=True, key=key).first()
        if found is None:
            raise RowProblem(UNKNOWN_CATEGORY, "category_key")
        return found, "category_key"
    if name:
        found = (
            Category.objects.filter(owner=owner)
            .annotate(lowered=Lower("name"))
            .filter(lowered=name.lower())
            .first()
        )
        if found is None:
            raise RowProblem(UNKNOWN_CATEGORY, "category_name")
        return found, "category_name"
    raise RowProblem("required", "category_key")


def _import_transaction(owner: User, raw: dict[str, Any], seen: Seen) -> None:
    serializer = shapes.TransactionRow(data=raw)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    category, column = _category_of(owner, data)
    account = Account.objects.filter(
        owner=owner, name__iexact=data["account_name"]
    ).first()
    if account is None:
        raise RowProblem(UNKNOWN_ACCOUNT, "account_name")
    money: Money = data["amount"]
    note = data.get("note", "")
    same = {
        "date": data["date"],
        "kind": data["kind"],
        "amount": money.minor,
        "currency": money.currency.value,
        "category": category,
        "account": account,
        "note": note,
    }
    key = (
        data["date"],
        data["kind"],
        money.minor,
        money.currency.value,
        category.pk,
        account.pk,
        note,
    )
    if seen.duplicate(
        key, lambda: Transaction.objects.filter(owner=owner, **same).count()
    ):
        raise RowProblem(DUPLICATE)
    try:
        ledger_services.create_transaction(
            owner=owner,
            data={
                "kind": data["kind"],
                "amount": money,
                "date": data["date"],
                "category_id": category.pk,
                "account_id": account.pk,
                "note": note,
            },
        )
    except ValidationError as error:
        raise problem_of(error, renames={"category_id": column}) from None


# --- debts ------------------------------------------------------------------------------------


class _Currency:
    """The currency of a debt that is not saved yet, for the serializer of its repayments."""

    def __init__(self, currency: str) -> None:
        self.currency = currency


def _import_debt(owner: User, raw: dict[str, Any], seen: Seen) -> None:
    serializer = shapes.DebtRow(data=raw)
    serializer.is_valid(raise_exception=True)
    data = dict(serializer.validated_data)
    items = data.pop("repayments", [])
    money: Money = data["amount"]
    context = {"debt": _Currency(money.currency.value)}
    repayments: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        shape = RepaymentInputSerializer(data=item, context=context)
        try:
            shape.is_valid(raise_exception=True)
        except ValidationError as error:
            raise problem_of(error, prefix=f"repayments.{index}") from None
        repayments.append(dict(shape.validated_data))

    note = data.get("note", "")
    due = data.get("due_date")
    same = {
        "direction": data["direction"],
        "person": data["person"],
        "amount": money.minor,
        "currency": money.currency.value,
        "start_date": data["start_date"],
        "due_date": due,
        "note": note,
    }
    key = tuple(same.values())
    if seen.duplicate(key, lambda: Debt.objects.filter(owner=owner, **same).count()):
        raise RowProblem(DUPLICATE)
    try:
        debt = debt_services.create_debt(owner=owner, data={**data, "note": note})
    except ValidationError as error:
        raise problem_of(error) from None
    for index, repayment in enumerate(repayments):
        try:
            debt_services.add_repayment(owner=owner, debt_id=debt.pk, data=repayment)
        except ValidationError as error:
            raise problem_of(error, prefix=f"repayments.{index}") from None


# --- salary months ----------------------------------------------------------------------------


def _salary_inputs(raw: dict[str, Any]) -> tuple[datetime.date, dict[str, Any]]:
    """The month and the inputs of the salary month API, from a row with the CSV's names and units."""
    unknown = shapes.unknown_field(raw, shapes.SALARY_COLUMNS)
    if unknown is not None:
        raise RowProblem("read_only", unknown)
    key = raw.get("month")
    if key is None:
        raise RowProblem("required", "month")
    found = _MONTH.fullmatch(key) if isinstance(key, str) else None
    if found is None:
        raise RowProblem("invalid", "month")
    month = datetime.date(int(found.group(1)), int(found.group(2)), 1)
    inputs = {
        name: value
        for name, value in raw.items()
        if name not in ("month", "extra_hours", "tax_rate", "fixed_items")
    }
    for column, name, convert in (
        ("extra_hours", "extra_hours_hundredths", hours_to_hundredths),
        ("tax_rate", "tax_rate_basis_points", percent_to_basis_points),
    ):
        if column in raw:
            try:
                inputs[name] = convert(raw[column])
            except InvalidUnit as error:
                raise RowProblem(error.code, column) from None
    return month, inputs


def _fixed_items(raw: Any, currency: Currency) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RowProblem("invalid", "fixed_items")
    items = []
    for index, item in enumerate(raw):
        where = f"fixed_items.{index}"
        if not isinstance(item, dict):
            raise RowProblem("invalid", where)
        unknown = shapes.unknown_field(item, shapes.FIXED_ITEM_KEYS)
        if unknown is not None:
            raise RowProblem("read_only", f"{where}.{unknown}")
        shape = FixedItemInputSerializer(
            data=item, context={"currency": currency.value}
        )
        try:
            shape.is_valid(raise_exception=True)
        except ValidationError as error:
            raise problem_of(error, prefix=where) from None
        items.append(dict(shape.validated_data))
    return items


def _import_salary_month(owner: User, raw: dict[str, Any], seen: Seen) -> None:
    month, inputs = _salary_inputs(raw)
    shape = SalaryMonthInputSerializer(data=inputs)
    try:
        shape.is_valid(raise_exception=True)
    except ValidationError as error:
        raise problem_of(error, renames=shapes.RENAMED) from None
    data = shape.validated_data
    items = _fixed_items(raw.get("fixed_items"), Currency(data["currency"]))
    if SalaryMonth.objects.filter(owner=owner, month=month).exists():
        raise RowProblem(DUPLICATE, "month")
    try:
        saved = salary_services.save_month(
            owner=owner, month=month, data=data, existing=None
        )
        for item in items:
            salary_services.add_item(month=saved, data=item)
        salary_services.checked(saved)  # the sums must stay within the largest amount
    except (ValidationError, APIException) as error:
        raise problem_of(error, renames=shapes.RENAMED) from None


_DATASETS: dict[str, Callable[[User, dict[str, Any], Seen], None]] = {
    "accounts": _import_account,
    "categories": _import_category,
    "transactions": _import_transaction,
    "debts": _import_debt,
    "salary_months": _import_salary_month,
}
DATASETS = tuple(_DATASETS)
DATASET_CHOICES = [(name, name) for name in DATASETS]


def run_import(owner: User, dataset: str, rows: list[dict[str, Any]]) -> ImportResult:
    """Write the rows that pass; report the others. The caller holds the transaction of the whole call."""
    User.objects.select_for_update().get(pk=owner.pk)  # one import per person at a time
    handle = _DATASETS[dataset]
    seen = Seen()
    result = ImportResult(dataset=dataset)
    for raw in rows:
        number = raw["row"]
        fields = {name: value for name, value in raw.items() if name != "row"}
        try:
            with (
                transaction.atomic()
            ):  # a savepoint: a failed row leaves nothing behind
                handle(owner, fields, seen)
        except (RowProblem, ValidationError, APIException) as error:
            found = problem_of(error)
            result.problems.append(Problem(number, found.code, found.attr))
        else:
            result.imported += 1
    result.problems.sort(key=lambda problem: problem.row)
    return result
