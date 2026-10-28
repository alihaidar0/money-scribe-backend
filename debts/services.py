"""Debts and repayments without HTTP (ADR 0005).

Every function that changes data runs inside the view's transaction and takes its locks in one order: the owner (to
count debts), then the debt, then a repayment. A repayment is written, and a debt is changed or deleted, under a row
lock on the debt, and the remaining amount is computed inside it, so two requests at once cannot overpay a debt or
lower it below what was repaid. A repayment write does not change the debt's own ``version``.

Nothing here logs a person's name or an amount.
"""

import datetime
from typing import Any

from django.db.models import Sum
from rest_framework.exceptions import ErrorDetail, NotFound, ValidationError

from common.money import Currency, Money
from config.error_codes import ErrorCode
from config.exceptions import ExceedsRemaining, conflict
from users.models import User

from .models import MAX_DEBTS, MAX_REPAYMENTS, Debt, Repayment


def _error(field: str, code: ErrorCode, detail: str) -> tuple[str, list[ErrorDetail]]:
    return field, [ErrorDetail(detail, code=code.value)]


def lock_owner(owner: User) -> None:
    """Take a row lock on the owner for the rest of the transaction (the caller is inside one)."""
    User.objects.select_for_update().get(pk=owner.pk)


def lock_debt(owner: User, debt_id: Any) -> Debt:
    """The person's debt under a row lock, or ``404`` (also for another person's debt)."""
    debt = Debt.objects.select_for_update().filter(owner=owner, pk=debt_id).first()
    if debt is None:
        raise NotFound()
    return debt


def lock_repayment(debt: Debt, repayment_id: Any) -> Repayment:
    """A repayment of this debt under a row lock, or ``404``. The caller holds the debt's lock."""
    repayment = (
        Repayment.objects.select_for_update().filter(debt=debt, pk=repayment_id).first()
    )
    if repayment is None:
        raise NotFound()
    return repayment


def repaid_of(debt: Debt, *, excluding: Repayment | None = None) -> int:
    """What the repayments of the debt add up to, in minor units, without ``excluding``."""
    repayments = Repayment.objects.filter(debt=debt)
    if excluding is not None:
        repayments = repayments.exclude(pk=excluding.pk)
    total: int = repayments.aggregate(total=Sum("amount"))["total"] or 0
    return total


# --- debts ------------------------------------------------------------------------------------


def create_debt(*, owner: User, data: dict[str, Any]) -> Debt:
    """Add a debt; the 501st is ``409 debt_limit_reached``. The caller is inside a transaction."""
    lock_owner(owner)
    if Debt.objects.filter(owner=owner).count() >= MAX_DEBTS:
        raise conflict(ErrorCode.DEBT_LIMIT_REACHED)
    errors = dates_error(data["start_date"], data.get("due_date"))
    if errors:
        raise ValidationError(errors)
    amount: Money = data["amount"]
    return Debt.objects.create(
        owner=owner,
        direction=data["direction"],
        person=data["person"],
        amount=amount.minor,
        currency=amount.currency.value,
        start_date=data["start_date"],
        due_date=data.get("due_date"),
        note=data.get("note", ""),
    )


def dates_error(
    start: datetime.date, due: datetime.date | None
) -> dict[str, list[ErrorDetail]]:
    """``due_before_start`` on ``due_date`` when the due day is before the start day."""
    if due is not None and due < start:
        return dict(
            [
                _error(
                    "due_date",
                    ErrorCode.DUE_BEFORE_START,
                    "The due date is before the start date.",
                )
            ]
        )
    return {}


def update_debt(*, debt: Debt, data: dict[str, Any]) -> Debt:
    """Apply a partial edit to a debt the caller has locked. A request that changes nothing saves nothing.

    The amount cannot go below what was repaid, and the start day cannot go past a repayment. Raising the amount above
    what was repaid reopens a settled debt: the status is derived, so nothing else changes. The ``direction`` may be
    corrected (the repayments and the figures do not depend on it); the ``currency`` is not editable (the serializer
    refuses it).
    """
    errors: dict[str, list[ErrorDetail]] = {}
    if "amount" in data:
        money: Money = data["amount"]
        if money.minor < repaid_of(debt):
            errors.update(
                [
                    _error(
                        "amount",
                        ErrorCode.AMOUNT_BELOW_REPAID,
                        "The amount is less than what was already repaid.",
                    )
                ]
            )
    start = data.get("start_date", debt.start_date)
    due = data.get("due_date", debt.due_date)
    if "start_date" in data:
        first = (
            Repayment.objects.filter(debt=debt)
            .order_by("date")
            .values_list("date", flat=True)
            .first()
        )
        if first is not None and start > first:
            errors.update(
                [
                    _error(
                        "start_date",
                        ErrorCode.START_AFTER_REPAYMENT,
                        "A repayment is dated before this start date.",
                    )
                ]
            )
    errors.update(dates_error(start, due))
    if errors:
        raise ValidationError(errors)

    changes: dict[str, Any] = {}
    if "amount" in data:
        changes["amount"] = data["amount"].minor
    for name in ("direction", "person", "start_date", "due_date", "note"):
        if name in data:
            changes[name] = data[name]
    changed = False
    for name, value in changes.items():
        if getattr(debt, name) != value:
            setattr(debt, name, value)
            changed = True
    if changed:
        debt.save()
    return debt


# --- repayments -------------------------------------------------------------------------------


def _check_repayment(
    debt: Debt, money: Money, date: datetime.date, *, others: int
) -> None:
    """The rules of a repayment: the day is not before the debt starts and the amount is within what remains."""
    errors: dict[str, list[ErrorDetail]] = {}
    if date < debt.start_date:
        errors.update(
            [
                _error(
                    "date",
                    ErrorCode.REPAYMENT_BEFORE_START,
                    "The repayment is dated before the debt starts.",
                )
            ]
        )
    if errors:
        raise ValidationError(errors)
    allowed = max(debt.amount - others, 0)
    if money.minor > allowed:
        raise ExceedsRemaining(Money(allowed, Currency(debt.currency)).to_string())


def add_repayment(*, owner: User, debt_id: Any, data: dict[str, Any]) -> Debt:
    """Add a repayment and return the debt, locked; the 201st is ``409 repayment_limit_reached``."""
    debt = lock_debt(owner, debt_id)
    if Repayment.objects.filter(debt=debt).count() >= MAX_REPAYMENTS:
        raise conflict(ErrorCode.REPAYMENT_LIMIT_REACHED)
    money: Money = data["amount"]
    _check_repayment(debt, money, data["date"], others=repaid_of(debt))
    Repayment.objects.create(debt=debt, amount=money.minor, date=data["date"])
    return debt


def update_repayment(*, debt: Debt, repayment: Repayment, data: dict[str, Any]) -> None:
    """Apply a partial edit to a repayment; the caller holds the locks of the debt and the repayment.

    The repayment's own previous amount does not count: the room for it is the debt minus the other repayments.
    """
    money: Money = data.get("amount", Money(repayment.amount, Currency(debt.currency)))
    date = data.get("date", repayment.date)
    _check_repayment(debt, money, date, others=repaid_of(debt, excluding=repayment))
    changed = False
    if money.minor != repayment.amount:
        repayment.amount = money.minor
        changed = True
    if date != repayment.date:
        repayment.date = date
        changed = True
    if changed:
        repayment.save()
