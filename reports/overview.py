"""The Overview of a month: what came in, what went out and what is left, in one currency (ADR 0007).

``balance`` = ``salary`` + ``recurring_income`` + ``income_transactions`` - ``fixed_costs`` - ``purchases`` +
``debt_repayments_received`` - ``debt_repayments_made``. The salary and the fixed items come from the saved month, through the
same function the month's own answer uses (``salary.presentation.part_figures``), so the two cannot disagree; they count
only when the saved month's currency is the one asked for. Transactions and repayments count by their calendar ``date``.
A debt that is created is not a movement of money; a repayment is. Nothing is added across currencies.
"""

import datetime
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from django.db.models import Sum

from common.enums import PaymentMode
from common.money import Currency, Money
from debts.models import Repayment
from ledger.models import Transaction
from salary.models import SalaryMonth
from salary.presentation import part_figures
from users.models import User

from .periods import (
    FIRST,
    SECOND,
    MonthPeriod,
    payment_mode_of,
    period_of,
)


@dataclass(frozen=True)
class Block:
    """The figures of the whole month or of one part, in the report currency."""

    salary: Money
    recurring_income: Money
    fixed_costs: Money
    income_transactions: Money
    purchases: Money
    debt_repayments_received: Money
    debt_repayments_made: Money

    @property
    def balance(self) -> Money:
        inflow = (
            self.salary
            + self.recurring_income
            + self.income_transactions
            + self.debt_repayments_received
        )
        outflow = self.fixed_costs + self.purchases + self.debt_repayments_made
        return inflow - outflow


@dataclass(frozen=True)
class Overview:
    month: datetime.date
    currency: Currency
    has_data: bool
    currencies: list[Currency]
    total: Block
    first_part: Block | None
    second_part: Block | None


def _sums(
    rows: Iterable[Mapping[str, Any]], key: str, currency: Currency
) -> dict[tuple[str, int], Money]:
    """``{(what, half): money}`` from rows of ``(what, half, total)``."""
    return {
        (row[key], int(row["half"])): Money(int(row["total"]), currency) for row in rows
    }


def _currencies_of_the_month(
    user: User, period: MonthPeriod, saved: SalaryMonth | None
) -> list[Currency]:
    """Every currency the month has something in: the saved month, a transaction, a repayment."""
    found = {
        *Transaction.objects.filter(
            owner=user, date__gte=period.first_day, date__lte=period.last_day
        )
        .order_by()
        .values_list("currency", flat=True)
        .distinct(),
        *Repayment.objects.filter(
            debt__owner=user, date__gte=period.first_day, date__lte=period.last_day
        )
        .order_by()
        .values_list("debt__currency", flat=True)
        .distinct(),
    }
    if saved is not None:
        found.add(saved.currency)
    return [Currency(code) for code in sorted(found)]


def overview(user: User, month: datetime.date, currency: Currency) -> Overview:
    """The Overview of the month whose first day is ``month``. Raises ``AmountOutOfRange`` for a sum past the largest."""
    period = period_of(month)
    saved = (
        SalaryMonth.objects.filter(owner=user, month=month)
        .prefetch_related("fixed_items")
        .first()
    )
    two = payment_mode_of(user, saved) == PaymentMode.TWO_PAYMENTS
    zero = Money.zero(currency)

    # Salary and fixed items: only a saved month in this currency.
    salary = {
        "total": (zero, zero, zero),
        "first": (zero, zero, zero),
        "second": (zero, zero, zero),
    }
    if saved is not None and saved.money_currency == currency:
        figures = part_figures(saved, saved.fixed_items.all())
        for name, part in (
            ("total", figures.total),
            ("first", figures.first),
            ("second", figures.second),
        ):
            if part is not None:
                salary[name] = (part.salary, part.income, part.expense)

    in_month = {"date__gte": period.first_day, "date__lte": period.last_day}
    transactions = _sums(
        (
            Transaction.objects.filter(owner=user, currency=currency.value, **in_month)
            .order_by()
            .annotate(half=period.half())
            .values("kind", "half")
            .annotate(total=Sum("amount"))
        ),
        "kind",
        currency,
    )
    repayments = _sums(
        (
            Repayment.objects.filter(
                debt__owner=user, debt__currency=currency.value, **in_month
            )
            .order_by()
            .annotate(half=period.half())
            .values("debt__direction", "half")
            .annotate(total=Sum("amount"))
        ),
        "debt__direction",
        currency,
    )

    def moved(
        table: dict[tuple[str, int], Money], what: str, halves: tuple[int, ...]
    ) -> Money:
        return Money.sum(currency, (table.get((what, half), zero) for half in halves))

    def block(name: str, halves: tuple[int, ...]) -> Block:
        pay, income, expense = salary[name]
        return Block(
            salary=pay,
            recurring_income=income,
            fixed_costs=expense,
            income_transactions=moved(transactions, "income", halves),
            purchases=moved(transactions, "expense", halves),
            debt_repayments_received=moved(repayments, "lent", halves),
            debt_repayments_made=moved(repayments, "borrowed", halves),
        )

    currencies = _currencies_of_the_month(user, period, saved)
    return Overview(
        month=month,
        currency=currency,
        has_data=bool(currencies),
        currencies=currencies,
        total=block("total", (FIRST, SECOND)),
        first_part=block("first", (FIRST,)) if two else None,
        second_part=block("second", (SECOND,)) if two else None,
    )
