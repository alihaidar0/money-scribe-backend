"""The month a report covers, its two parts and the settings that decide them (ADR 0007).

A month paid twice is cut at the calendar day of a record: days 1 to 15 are the first part, day 16 to the end of the
month the second. The cut is on the transaction's or the repayment's own ``date``, never on an instant, so it does not
move with a time zone.
"""

import calendar
import datetime
from dataclasses import dataclass

from django.db.models import Case, IntegerField, Value, When
from django.db.models.expressions import Combinable

from common.enums import PaymentMode
from common.money import Currency
from preferences import services as preferences
from salary.models import SalaryMonth
from users.models import User

FIRST_PART_LAST_DAY = 15
FIRST, SECOND = 1, 2


@dataclass(frozen=True)
class MonthPeriod:
    first_day: datetime.date
    middle: datetime.date  # the last day of the first part
    last_day: datetime.date

    def half(self, field: str = "date") -> Combinable:
        """An expression that is 1 for a day of the first part and 2 for a day of the second."""
        return Case(
            When(**{f"{field}__lte": self.middle}, then=Value(FIRST)),
            default=Value(SECOND),
            output_field=IntegerField(),
        )


def period_of(month: datetime.date) -> MonthPeriod:
    """The days of the month whose first day is given."""
    last = calendar.monthrange(month.year, month.month)[1]
    return MonthPeriod(
        first_day=month,
        middle=month.replace(day=FIRST_PART_LAST_DAY),
        last_day=month.replace(day=last),
    )


def default_currency_of(user: User) -> Currency:
    """The Settings default currency, or the app's own default for one never set."""
    return preferences.set_values(user).default_currency or preferences.DEFAULT_CURRENCY


def payment_mode_of(user: User, saved: SalaryMonth | None) -> PaymentMode:
    """The mode that says whether the month has two parts.

    A saved month keeps the mode it was saved with, even if the schedule changed later; a month never saved follows the
    Settings payment mode.
    """
    if saved is not None:
        return PaymentMode(saved.payment_mode)
    return preferences.set_values(user).payment_mode or preferences.DEFAULT_PAYMENT_MODE
