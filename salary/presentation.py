"""What the API says about a month: the stored month and its items turned into the figures of the contract.

Everything here is derived, never stored: the fixed-item totals per part and the balances (ADR 0007). With one
payment there is a single part, ``first``, that counts every item whatever its stored ``part``, and ``second`` is
``None``. With two payments each part counts its own items, and the whole-month balance is the total after tax plus
all the fixed income minus all the fixed expenses.

A month and its items were valid when they were written, but the sums could still pass the largest amount (a
hundred items of 10**15); that raises ``AmountOutOfRange`` here, and the writing views turn it into ``400`` and roll
the change back, so no stored month ever fails to be read.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from common.enums import PaymentMode
from common.money import Currency, Money

from .models import FixedItem, ItemKind, Part, SalaryMonth


def ordered(items: Iterable[FixedItem]) -> list[FixedItem]:
    """The items in the order they were added."""
    return sorted(items, key=lambda item: (item.created_at, item.id))


def present_item(item: FixedItem) -> SimpleNamespace:
    """A fixed item as the serializer reads it: the amount as ``Money`` and the currency of its month."""
    return SimpleNamespace(
        id=item.id,
        name=item.name,
        amount=item.money,
        currency=item.currency,
        kind=item.kind,
        part=item.part,
        version=item.version,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _totals(currency: Currency, items: Iterable[FixedItem]) -> dict[str, Money]:
    items = list(items)
    return {
        "income": Money.sum(
            currency, (i.money for i in items if i.kind == ItemKind.INCOME)
        ),
        "expense": Money.sum(
            currency, (i.money for i in items if i.kind == ItemKind.EXPENSE)
        ),
    }


@dataclass(frozen=True, slots=True)
class PartFigures:
    """What one part of a month brings in and takes out: its salary payment and its fixed items."""

    salary: Money
    income: Money
    expense: Money

    @property
    def balance(self) -> Money:
        """The salary plus the fixed income minus the fixed expenses."""
        return self.salary + self.income - self.expense


@dataclass(frozen=True, slots=True)
class MonthFigures:
    """The figures of a saved month by part: the whole month, the first part and, with two payments, the second.

    With one payment there is a single part, ``first``, that counts every item whatever its stored ``part``. This is the
    one place the balances of a month are worked out: the month's own answer and the Overview both read it.
    """

    total: PartFigures
    first: PartFigures
    second: PartFigures | None
    first_totals: dict[str, Money]
    second_totals: dict[str, Money] | None


def part_figures(month: SalaryMonth, items: Iterable[FixedItem]) -> MonthFigures:
    currency = month.money_currency
    items = list(items)
    if month.payment_mode == PaymentMode.TWO_PAYMENTS:
        first_items = [i for i in items if i.part == Part.FIRST]
        second_items: list[FixedItem] | None = [
            i for i in items if i.part == Part.SECOND
        ]
    else:
        first_items, second_items = items, None
    first_totals = _totals(currency, first_items)
    second_totals = None if second_items is None else _totals(currency, second_items)
    all_totals = _totals(currency, items)
    second_payment = (
        month.money(month.second_payment) if month.second_payment is not None else None
    )
    second = (
        None
        if second_payment is None or second_totals is None
        else PartFigures(
            second_payment, second_totals["income"], second_totals["expense"]
        )
    )
    return MonthFigures(
        total=PartFigures(
            month.money(month.total_after_tax),
            all_totals["income"],
            all_totals["expense"],
        ),
        first=PartFigures(
            month.money(month.first_payment),
            first_totals["income"],
            first_totals["expense"],
        ),
        second=second,
        first_totals=first_totals,
        second_totals=second_totals,
    )


def build_month(month: SalaryMonth, items: Iterable[FixedItem]) -> dict[str, Any]:
    """The month as the contract shows it, with ``Money`` where the serializers write an amount."""
    items = ordered(items)
    figures = part_figures(month, items)
    first_totals = figures.first_totals
    second_totals = figures.second_totals
    balances: dict[str, Money | None] = {
        "first": figures.first.balance,
        "second": None if figures.second is None else figures.second.balance,
        "month": figures.total.balance,
    }
    first_payment = month.money(month.first_payment)
    second_payment = (
        month.money(month.second_payment) if month.second_payment is not None else None
    )
    payments = [{"part": Part.FIRST.value, "amount": first_payment}]
    if second_payment is not None:
        payments.append({"part": Part.SECOND.value, "amount": second_payment})

    return {
        "id": month.id,
        "month": f"{month.month:%Y-%m}",
        "currency": month.currency,
        "base_salary": month.money(month.base_salary),
        "food_per_day": month.money(month.food_per_day),
        "working_days_in_month": month.working_days_in_month,
        "working_hours_in_month": month.working_hours_in_month,
        "extra_hours_hundredths": month.extra_hours_hundredths,
        "tax_rate_basis_points": month.tax_rate_basis_points,
        "payment_mode": month.payment_mode,
        "working_days_first_half": month.working_days_first_half,
        "first_pay_day": month.first_pay_day,
        "second_pay_day": month.second_pay_day,
        "calculation_version": month.calculation_version,
        "results": {
            "food_allowance": month.money(month.food_allowance),
            "extra_hours_pay": month.money(month.extra_hours_pay),
            "income_before_tax": month.money(month.income_before_tax),
            "tax": month.money(month.tax),
            "total_after_tax": month.money(month.total_after_tax),
        },
        "payments": payments,
        "fixed_items": [present_item(i) for i in items],
        "fixed_totals": {"first": first_totals, "second": second_totals},
        "balances": balances,
        "version": month.version,
        "created_at": month.created_at,
        "updated_at": month.updated_at,
    }
