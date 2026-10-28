"""The Overview of a month: what came in, what went out and what is left, in one currency (ADR 0007).

``balance`` = ``salary`` + ``recurring_income`` + ``income_transactions`` - ``fixed_costs`` - ``purchases`` +
``debt_repayments_received`` - ``debt_repayments_made``. The salary and the fixed items come from the saved month, through the
same function the month's own answer uses (``salary.presentation.part_figures``), so the two cannot disagree; they count
only when the saved month's currency is the one asked for. Repayments and expenses count by their calendar ``date``,
cut through the pay cycle for a month paid twice; an income joins the part it was put on or the nearest pay day
(``reports.cuts``, ADR 0023).
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
from preferences.services import pay_days_of
from salary.models import SalaryMonth
from salary.presentation import part_figures
from users.models import User

from .cuts import transaction_rows
from .periods import (
    FIRST,
    SECOND,
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
    """``{(what, half): money}`` from rows of ``(what, half, total)``; rows of the same key are added."""
    sums: dict[tuple[str, int], int] = {}
    for row in rows:
        at = (row[key], int(row["half"]))
        sums[at] = sums.get(at, 0) + int(row["total"])
    return {at: Money(minor, currency) for at, minor in sums.items()}


def overview(user: User, month: datetime.date, currency: Currency) -> Overview:
    """The Overview of the month whose first day is ``month``. Raises ``AmountOutOfRange`` for a sum past the largest."""
    saved = (
        SalaryMonth.objects.filter(owner=user, month=month)
        .prefetch_related("fixed_items")
        .first()
    )
    two = payment_mode_of(user, saved) == PaymentMode.TWO_PAYMENTS
    pay_days = pay_days_of(user) if two else None
    period = period_of(month, pay_days)
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
    found = transaction_rows(user, period, pay_days)
    transactions = _sums(
        (row for row in found if row["currency"] == currency.value), "kind", currency
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

    repaid_in = {
        *Repayment.objects.filter(debt__owner=user, **in_month)
        .order_by()
        .values_list("debt__currency", flat=True)
        .distinct()
    }
    found_in = {row["currency"] for row in found} | repaid_in
    if saved is not None:
        found_in.add(saved.currency)
    currencies = [Currency(code) for code in sorted(found_in)]
    return Overview(
        month=month,
        currency=currency,
        has_data=bool(currencies),
        currencies=currencies,
        total=block("total", (FIRST, SECOND)),
        first_part=block("first", (FIRST,)) if two else None,
        second_part=block("second", (SECOND,)) if two else None,
    )
