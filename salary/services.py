"""The salary rules (ADR 0007), apart from HTTP: saving a month, its fixed items, the defaults and the copy.

Every function that changes data runs inside the view's transaction. A month is locked (``select_for_update``) while
its items are counted or its currency is changed, so two requests cannot both pass the limit of 100 items or both
change the currency under an item.
"""

import datetime
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from rest_framework.exceptions import ErrorDetail, NotFound, ValidationError

from common.enums import PaymentMode
from common.money import AmountOutOfRange, Currency, Money
from config.error_codes import ErrorCode
from config.exceptions import conflict
from users.models import User

from .calculation import SalaryInputs, calculate
from .models import (
    CALCULATION_VERSION,
    MAX_FIXED_ITEMS,
    FixedItem,
    SalaryMonth,
)
from .presentation import build_month, ordered

# The app's own defaults, used when neither the Settings (a later step) nor an earlier month say anything.
DEFAULT_CURRENCY = Currency.RUB
DEFAULT_PAYMENT_MODE = PaymentMode.ONE_PAYMENT
DEFAULT_FIRST_PAY_DAY = 20
DEFAULT_SECOND_PAY_DAY = 5
DEFAULT_TAX_RATE_BASIS_POINTS = 1300


def _too_large() -> ValidationError:
    return ValidationError(
        [
            ErrorDetail(
                "A figure of this month is larger than the largest amount.",
                code=ErrorCode.INVALID_AMOUNT.value,
            )
        ]
    )


def month_of(owner: User, month: datetime.date) -> SalaryMonth:
    """The user's saved month, or ``404``."""
    found = SalaryMonth.objects.filter(owner=owner, month=month).first()
    if found is None:
        raise NotFound("This month is not saved.")
    return found


def lock_month(owner: User, month: datetime.date) -> SalaryMonth:
    """As :func:`month_of`, with the row locked until the transaction ends."""
    found = (
        SalaryMonth.objects.select_for_update().filter(owner=owner, month=month).first()
    )
    if found is None:
        raise NotFound("This month is not saved.")
    return found


def checked(month: SalaryMonth) -> dict[str, Any]:
    """The month as the contract shows it; ``400 invalid_amount`` if its sums pass the largest amount.

    Called inside the transaction of every write, so a change that would make the month unreadable is refused
    and rolled back instead of stored.
    """
    items = list(month.fixed_items.all())
    try:
        return build_month(month, items)
    except AmountOutOfRange:
        raise _too_large() from None


def save_month(
    *,
    owner: User,
    month: datetime.date,
    data: dict[str, Any],
    existing: SalaryMonth | None,
) -> SalaryMonth:
    """Create the month, or replace the inputs of ``existing``, and store what the calculation gives."""
    mode = PaymentMode(data["payment_mode"])
    inputs = SalaryInputs(
        base_salary=data["base_salary"],
        food_per_day=data["food_per_day"],
        working_days_in_month=data["working_days_in_month"],
        working_hours_in_month=data["working_hours_in_month"],
        extra_hours_hundredths=data["extra_hours_hundredths"],
        tax_rate_basis_points=data["tax_rate_basis_points"],
        payment_mode=mode,
        working_days_first_half=(
            data.get("working_days_first_half")
            if mode is PaymentMode.TWO_PAYMENTS
            else None
        ),
    )
    try:
        results = calculate(inputs)
    except AmountOutOfRange:
        raise _too_large() from None
    values: dict[str, Any] = {
        "currency": data["currency"],
        "base_salary": inputs.base_salary.minor,
        "food_per_day": inputs.food_per_day.minor,
        "working_days_in_month": inputs.working_days_in_month,
        "working_hours_in_month": inputs.working_hours_in_month,
        "extra_hours_hundredths": inputs.extra_hours_hundredths,
        "tax_rate_basis_points": inputs.tax_rate_basis_points,
        "payment_mode": mode.value,
        "working_days_first_half": inputs.working_days_first_half,
        "first_pay_day": data["first_pay_day"],
        "second_pay_day": data["second_pay_day"],
        "calculation_version": CALCULATION_VERSION,
        "food_allowance": results.food_allowance.minor,
        "extra_hours_pay": results.extra_hours_pay.minor,
        "income_before_tax": results.income_before_tax.minor,
        "tax": results.tax.minor,
        "total_after_tax": results.total_after_tax.minor,
        "first_payment": results.first_payment.minor,
        "second_payment": (
            None if results.second_payment is None else results.second_payment.minor
        ),
    }
    if existing is None:
        return SalaryMonth.objects.create(owner=owner, month=month, **values)
    if existing.currency != values["currency"] and existing.fixed_items.exists():
        raise conflict(ErrorCode.MONTH_HAS_FIXED_ITEMS)
    for field, value in values.items():
        setattr(existing, field, value)
    existing.save()
    return existing


# --- fixed items ------------------------------------------------------------------------------


def add_item(*, month: SalaryMonth, data: dict[str, Any]) -> FixedItem:
    """Add an item to a month that the caller has locked; the 101st is ``409 fixed_item_limit_reached``."""
    if month.fixed_items.count() >= MAX_FIXED_ITEMS:
        raise conflict(ErrorCode.FIXED_ITEM_LIMIT_REACHED)
    amount: Money = data["amount"]
    return FixedItem.objects.create(
        salary_month=month,
        name=data["name"],
        amount=amount.minor,
        kind=data["kind"],
        part=data["part"],
    )


def update_item(*, item: FixedItem, data: dict[str, Any]) -> FixedItem:
    """Apply a partial edit to an item the caller has locked."""
    if "name" in data:
        item.name = data["name"]
    if "amount" in data:
        item.amount = data["amount"].minor
    if "kind" in data:
        item.kind = data["kind"]
    if "part" in data:
        item.part = data["part"]
    item.save()
    return item


def copy_items(
    *, owner: User, month: datetime.date, source: datetime.date | None
) -> SalaryMonth:
    """Copy the fixed items of another saved month into this one, and answer the target month.

    The items are added to the ones already there, with new ids, version 1 and the same name, amount, kind and
    part. Nothing is copied if the total would pass 100.
    """
    target = lock_month(owner, month)
    if source is None:
        origin = (
            SalaryMonth.objects.filter(owner=owner, month__lt=month)
            .order_by("-month")
            .first()
        )
        if origin is None:
            raise NotFound("There is no earlier saved month to copy from.")
    else:
        if source == month:
            raise ValidationError(
                {
                    "from": [
                        ErrorDetail(
                            "Choose another month than this one.", code="invalid"
                        )
                    ]
                }
            )
        origin = month_of(owner, source)
    if origin.currency != target.currency:
        raise ValidationError(
            {
                "from": [
                    ErrorDetail(
                        "The month has another currency.",
                        code=ErrorCode.CURRENCY_MISMATCH.value,
                    )
                ]
            }
        )
    items = ordered(origin.fixed_items.all())
    if target.fixed_items.count() + len(items) > MAX_FIXED_ITEMS:
        raise conflict(ErrorCode.FIXED_ITEM_LIMIT_REACHED)
    with transaction.atomic():
        for item in items:
            FixedItem.objects.create(
                salary_month=target,
                name=item.name,
                amount=item.amount,
                kind=item.kind,
                part=item.part,
            )
    return target


# --- the defaults -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingsDefaults:
    """What the person set in the Settings; ``None`` is "never set" and the next source decides."""

    currency: Currency | None = None
    payment_mode: PaymentMode | None = None
    first_pay_day: int | None = None
    second_pay_day: int | None = None


def defaults_for(
    owner: User, month: datetime.date, *, settings: SettingsDefaults | None = None
) -> dict[str, Any]:
    """The inputs that pre-fill the form of a month (docs/api-fields.md).

    Each value has its own sources, in this order. The currency: the Settings, then the most recent saved month, then
    ``RUB``. The pay schedule (``payment_mode`` and each pay day on its own): the Settings, then that month, then the
    app's defaults. Only what the person has actually set counts as the Settings: an untouched one falls through. The
    amounts of the most recent month (``base_salary``, ``food_per_day``) carry over only when the currency is the same,
    because a rouble amount is not a dollar amount; the hours and the tax rate always carry. The working days and the
    first-half days are never carried and the extra hours start at zero. The month being asked for is never its own
    source.
    """
    chosen = settings or SettingsDefaults()
    latest = (
        SalaryMonth.objects.filter(owner=owner)
        .exclude(month=month)
        .order_by("-month")
        .first()
    )
    currency = chosen.currency or (
        latest.money_currency if latest else DEFAULT_CURRENCY
    )
    carries = latest is not None and latest.money_currency is currency
    mode = chosen.payment_mode.value if chosen.payment_mode else None
    return {
        "currency": currency.value,
        "base_salary": latest.money(latest.base_salary) if latest and carries else None,
        "food_per_day": latest.money(latest.food_per_day)
        if latest and carries
        else None,
        "working_days_in_month": None,
        "working_hours_in_month": latest.working_hours_in_month if latest else None,
        "extra_hours_hundredths": 0,
        "tax_rate_basis_points": (
            latest.tax_rate_basis_points if latest else DEFAULT_TAX_RATE_BASIS_POINTS
        ),
        "payment_mode": mode
        or (latest.payment_mode if latest else DEFAULT_PAYMENT_MODE.value),
        "working_days_first_half": None,
        "first_pay_day": chosen.first_pay_day
        or (latest.first_pay_day if latest else DEFAULT_FIRST_PAY_DAY),
        "second_pay_day": chosen.second_pay_day
        or (latest.second_pay_day if latest else DEFAULT_SECOND_PAY_DAY),
    }
