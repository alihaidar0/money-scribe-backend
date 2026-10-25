"""The month report: what was bought and what came in as income, by category, in one currency.

It reads transactions only. The salary, the recurring income and the fixed costs are not in it (the Overview has them);
``income_transactions`` and ``purchases`` are the same figures as the Overview's lines of the same name, for the same
month, part and currency. A month paid twice is also cut into its two parts by the calendar day of each transaction.
"""

import datetime
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from django.db.models import Sum

from common.enums import PaymentMode
from common.money import Currency, Money
from ledger.models import Transaction
from salary.models import SalaryMonth
from users.models import User

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
    period = period_of(month)
    in_month = Transaction.objects.filter(
        owner=user, date__gte=period.first_day, date__lte=period.last_day
    ).order_by()
    saved = SalaryMonth.objects.filter(owner=user, month=month).first()
    two = payment_mode_of(user, saved) == PaymentMode.TWO_PAYMENTS

    rows = list(
        in_month.filter(currency=currency.value)
        .annotate(half=period.half())
        .values(
            "kind",
            "half",
            "category_id",
            "category__key",
            "category__name",
            "category__icon",
        )
        .annotate(total=Sum("amount"))
    )
    currencies = [
        Currency(code)
        for code in sorted(in_month.values_list("currency", flat=True).distinct())
    ]
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
