"""The debts report: what is owed to the person and what they owe, by currency, with the open and overdue debts.

Settled debts are not listed. The status is counted in the person's day, as everywhere else (``debts.presentation``).
It is not paginated: a person has at most 500 debts. Nothing is added across currencies.
"""

import datetime
import uuid
from dataclasses import dataclass

from common.money import Currency, Money
from debts.models import Debt, Direction, Status
from debts.presentation import status_of
from debts.queries import with_repaid
from users.models import User


@dataclass(frozen=True)
class DebtLine:
    id: uuid.UUID
    person: str
    direction: str
    remaining: Money
    status: str
    due_date: datetime.date | None


@dataclass(frozen=True)
class CurrencyDebts:
    currency: Currency
    owed_to_you: Money
    you_owe: Money
    overdue: list[DebtLine]
    open: list[DebtLine]


@dataclass(frozen=True)
class DebtsReport:
    has_data: bool
    by_currency: list[CurrencyDebts]


def debts_report(user: User, today: datetime.date) -> DebtsReport:
    """The report of the person's debts on the day ``today``."""
    debts = list(with_repaid(Debt.objects.filter(owner=user)))
    lines: dict[str, list[DebtLine]] = {}
    for debt in debts:
        status = status_of(
            amount=debt.amount,
            repaid=debt.repaid_total,  # type: ignore[attr-defined]  # annotated by with_repaid
            due_date=debt.due_date,
            today=today,
        )
        if status == Status.SETTLED:
            continue
        lines.setdefault(debt.currency, []).append(
            DebtLine(
                id=debt.id,
                person=debt.person,
                direction=debt.direction,
                remaining=Money(
                    debt.amount - debt.repaid_total,  # type: ignore[attr-defined]  # annotated by with_repaid
                    Currency(debt.currency),
                ),
                status=status.value,
                due_date=debt.due_date,
            )
        )
    blocks = []
    for code in sorted(lines):
        currency = Currency(code)
        found = lines[code]
        blocks.append(
            CurrencyDebts(
                currency=currency,
                owed_to_you=Money.sum(
                    currency,
                    (
                        line.remaining
                        for line in found
                        if line.direction == Direction.LENT
                    ),
                ),
                you_owe=Money.sum(
                    currency,
                    (
                        line.remaining
                        for line in found
                        if line.direction == Direction.BORROWED
                    ),
                ),
                # The oldest due day first; an open debt with no due day comes last. The id breaks a tie.
                overdue=sorted(
                    (line for line in found if line.status == Status.OVERDUE),
                    key=lambda line: (line.due_date or datetime.date.max, line.id),
                ),
                open=sorted(
                    (line for line in found if line.status == Status.OPEN),
                    key=lambda line: (
                        line.due_date is None,
                        line.due_date or datetime.date.max,
                        line.id,
                    ),
                ),
            )
        )
    return DebtsReport(has_data=bool(debts), by_currency=blocks)
