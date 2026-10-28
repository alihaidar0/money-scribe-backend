"""The month report: what was bought and what came in as income, by category, in one currency.

It reads transactions only. The salary, the recurring income and the fixed costs are not in it (the Overview has them);
``income_transactions`` and ``purchases`` are the same figures as the Overview's lines of the same name, for the same
month, part and currency. A month paid twice follows the pay cycle: expenses are cut into its two parts by their
calendar day, an income joins the part it was put on or the nearest pay day (ADR 0023).
"""

import datetime
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from common.enums import PaymentMode
from common.money import Currency, Money
from preferences.services import pay_days_of
from salary.models import SalaryMonth
from users.models import User

from .cuts import transaction_rows
from .periods import FIRST, SECOND, payment_mode_of, period_of


@dataclass(frozen=True)
class CategorySpend:
    category_id: uuid.UUID
    category_key: str | None
    name: str
    icon: str
    amount: Money


@dataclass(frozen=True)
class Slice:
    """The figures of the whole month or of one part."""

    income_transactions: Money
    purchases: Money
    categories: list[CategorySpend]


@dataclass(frozen=True)
class MonthReport:
    month: datetime.date
    currency: Currency
    has_data: bool
    currencies: list[Currency]
    income_transactions: Money
    purchases: Money
    categories: list[CategorySpend]
    first_part: Slice | None
    second_part: Slice | None


def _slice(
    rows: Iterable[Mapping[str, Any]], currency: Currency, halves: tuple[int, ...]
) -> Slice:
    income = 0
    purchases = 0
    spent: dict[uuid.UUID, CategorySpend] = {}
    for row in rows:
        if row["half"] not in halves:
            continue
        minor = int(row["total"])
        if row["kind"] == "income":
            income += minor
            continue
        purchases += minor
        key = row["category_id"]
        before = spent.get(key)
        spent[key] = CategorySpend(
            category_id=key,
            category_key=row["category__key"],
            name=row["category__name"],
            icon=row["category__icon"],
            amount=Money(minor + (before.amount.minor if before else 0), currency),
        )
    # The biggest first; the id breaks a tie, so the order is the same on every read.
    ordered = sorted(spent.values(), key=lambda c: (-c.amount.minor, c.category_id))
    return Slice(Money(income, currency), Money(purchases, currency), ordered)


def month_report(user: User, month: datetime.date, currency: Currency) -> MonthReport:
    """The report of the month whose first day is ``month``. Raises ``AmountOutOfRange`` for a sum past the largest."""
    saved = SalaryMonth.objects.filter(owner=user, month=month).first()
    two = payment_mode_of(user, saved) == PaymentMode.TWO_PAYMENTS
    pay_days = pay_days_of(user) if two else None
    found = transaction_rows(
        user, period_of(month, pay_days), pay_days, by_category=True
    )
    rows = [row for row in found if row["currency"] == currency.value]
    currencies = [Currency(code) for code in sorted({row["currency"] for row in found})]
    whole = _slice(rows, currency, (FIRST, SECOND))
    return MonthReport(
        month=month,
        currency=currency,
        has_data=bool(currencies),
        currencies=currencies,
        income_transactions=whole.income_transactions,
        purchases=whole.purchases,
        categories=whole.categories,
        first_part=_slice(rows, currency, (FIRST,)) if two else None,
        second_part=_slice(rows, currency, (SECOND,)) if two else None,
    )
