"""What the API says about a debt: the stored debt and its repayments turned into the figures of the contract.

``repaid``, ``remaining`` and ``status`` are derived, never stored. ``remaining`` is the amount minus what was repaid
and never below zero (a repayment cannot overpay, so it is zero exactly when the debt is settled). A debt with a due
day before ``today`` that is not settled is overdue; ``today`` is the person's own day, never the server's.
"""

import datetime
from collections.abc import Iterable
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from common.money import Currency, Money
from users.models import User

from .models import Debt, Repayment, Status


def today_of(user: User) -> datetime.date:
    """The current calendar day where the person is (their ``time_zone``; UTC when it is not a known zone)."""
    try:
        zone = ZoneInfo(user.time_zone)
    except ZoneInfoNotFoundError, ValueError:
        zone = ZoneInfo("UTC")
    return timezone.now().astimezone(zone).date()


def ordered(repayments: Iterable[Repayment]) -> list[Repayment]:
    """The repayments by date, then id (the order of the list and of the debt)."""
    return sorted(repayments, key=lambda repayment: (repayment.date, repayment.id))


def status_of(
    *,
    amount: int,
    repaid: int,
    due_date: datetime.date | None,
    today: datetime.date,
) -> Status:
    if repaid >= amount:
        return Status.SETTLED
    if due_date is not None and due_date < today:
        return Status.OVERDUE
    return Status.OPEN


def present_repayment(repayment: Repayment, currency: Currency) -> SimpleNamespace:
    return SimpleNamespace(
        id=repayment.id,
        amount=Money(repayment.amount, currency),
        date=repayment.date,
        version=repayment.version,
        created_at=repayment.created_at,
        updated_at=repayment.updated_at,
    )


def present_debt(debt: Debt, today: datetime.date) -> SimpleNamespace:
    """A debt as the serializer reads it: ``Money`` for every amount and the derived figures filled in.

    The repayments are read from ``debt.repayments``; a list that prefetched them makes this free of queries.
    """
    currency = Currency(debt.currency)
    repayments = ordered(debt.repayments.all())
    repaid = sum(repayment.amount for repayment in repayments)
    return SimpleNamespace(
        id=debt.id,
        direction=debt.direction,
        person=debt.person,
        amount=debt.money,
        currency=debt.currency,
        start_date=debt.start_date,
        due_date=debt.due_date,
        note=debt.note,
        repaid=Money(repaid, currency),
        remaining=Money(max(debt.amount - repaid, 0), currency),
        status=status_of(
            amount=debt.amount, repaid=repaid, due_date=debt.due_date, today=today
        ),
        repayments=[present_repayment(r, currency) for r in repayments],
        version=debt.version,
        created_at=debt.created_at,
        updated_at=debt.updated_at,
    )
