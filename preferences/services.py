"""The Settings, apart from HTTP (docs/api-fields.md)."""

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from rest_framework.exceptions import ErrorDetail, ValidationError

from common.enums import PaymentMode
from common.money import Currency
from common.pay_cycle import DEFAULT_PAY_DAYS, PayDays
from config.error_codes import ErrorCode
from users.models import User

from .models import Preferences

# The app's own defaults: what an answer shows for a field that was never set.
DEFAULT_CURRENCY = Currency.RUB
DEFAULT_PAYMENT_MODE = PaymentMode.ONE_PAYMENT
DEFAULT_FIRST_PAY_DAY = DEFAULT_PAY_DAYS.first
DEFAULT_SECOND_PAY_DAY = DEFAULT_PAY_DAYS.second

TEXT_FIELDS = ("default_currency", "payment_mode")
STORED_FIELDS = (
    "default_currency",
    "payment_mode",
    "first_pay_day",
    "second_pay_day",
    "remind_on_pay_days",
)


@dataclass(frozen=True)
class SetValues:
    """What the person has actually set; ``None`` is "never set" (not the app's default)."""

    default_currency: Currency | None = None
    payment_mode: PaymentMode | None = None
    first_pay_day: int | None = None
    second_pay_day: int | None = None


def lock_or_create(user: User) -> Preferences:
    """The user's row, made on first use and locked until the end of the transaction."""
    prefs, _ = Preferences.objects.select_for_update().get_or_create(user=user)
    return prefs


def set_values(user: User) -> SetValues:
    """What the user has set, without creating a row (the salary defaults read it)."""
    prefs = Preferences.objects.filter(user=user).first()
    if prefs is None:
        return SetValues()
    return SetValues(
        default_currency=Currency(prefs.default_currency)
        if prefs.default_currency
        else None,
        payment_mode=PaymentMode(prefs.payment_mode) if prefs.payment_mode else None,
        first_pay_day=prefs.first_pay_day,
        second_pay_day=prefs.second_pay_day,
    )


def pay_days_of(user: User) -> PayDays:
    """The pay days the reports cut a month by: the Settings, the app's default for a day never set.

    A pair that is not ordered (the second day before the first) cannot be saved any more; one stored earlier falls
    back to the defaults, so a report never fails on it.
    """
    values = set_values(user)
    pair = PayDays(
        first=values.first_pay_day or DEFAULT_FIRST_PAY_DAY,
        second=values.second_pay_day or DEFAULT_SECOND_PAY_DAY,
    )
    return pair if pair.is_ordered else DEFAULT_PAY_DAYS


def effective(user: User, prefs: Preferences) -> dict[str, Any]:
    """Every setting as the API shows it: the stored value, or the app's default for one never set."""
    return {
        "language": user.language,
        "time_zone": user.time_zone,
        "default_currency": prefs.default_currency or DEFAULT_CURRENCY.value,
        "payment_mode": prefs.payment_mode or DEFAULT_PAYMENT_MODE.value,
        "first_pay_day": prefs.first_pay_day or DEFAULT_FIRST_PAY_DAY,
        "second_pay_day": prefs.second_pay_day or DEFAULT_SECOND_PAY_DAY,
        "remind_on_pay_days": prefs.remind_on_pay_days,
        "version": prefs.version,
        "updated_at": prefs.updated_at,
    }


def _ensure_pay_days_ordered(prefs: Preferences, data: dict[str, Any]) -> None:
    """``400 invalid_pay_days`` when the pay days that would result are not ordered (ADR 0023).

    The check is on the pair as it would be stored, a day never set counting as the app's default, so changing one
    day alone can be refused because of the other.
    """
    if "first_pay_day" not in data and "second_pay_day" not in data:
        return
    first = data.get("first_pay_day", prefs.first_pay_day) or DEFAULT_FIRST_PAY_DAY
    second = data.get("second_pay_day", prefs.second_pay_day) or DEFAULT_SECOND_PAY_DAY
    if not PayDays(first, second).is_ordered:
        raise ValidationError(
            {
                "second_pay_day": [
                    ErrorDetail(
                        "The second pay day must be before the first.",
                        code=ErrorCode.INVALID_PAY_DAYS.value,
                    )
                ]
            }
        )


def apply(user: User, prefs: Preferences, data: dict[str, Any]) -> Preferences:
    """Change the fields sent (and only those). Nothing sent changes nothing, not even the version.

    The language and the time zone go to the user, the rest to the row, in the caller's transaction, so a refused
    change leaves both as they were.
    """
    if not data:
        return prefs
    _ensure_pay_days_ordered(prefs, data)
    user_fields: list[str] = [
        name for name in ("language", "time_zone") if name in data
    ]
    for name in user_fields:
        setattr(user, name, data[name])
    if user_fields:
        user.save(update_fields=user_fields)
    for name in STORED_FIELDS:
        if name in data:
            value = data[name]
            # "Never set" is an empty string for the two text columns and NULL for the days.
            setattr(prefs, name, "" if value is None and name in TEXT_FIELDS else value)
    prefs.save()
    return prefs


def read(user: User) -> Preferences:
    """The user's row for a read; made on the first read, in its own short transaction."""
    with transaction.atomic():
        prefs = lock_or_create(user)
    return prefs
