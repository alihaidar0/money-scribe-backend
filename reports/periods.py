"""The days a report covers, its two parts and the settings that decide them (ADR 0007, 0023).

A month paid once is the calendar month. A month paid twice follows the pay cycle: its first part runs from the first
pay day of the month to the day before the second pay day of the next month, and its second part from that day to the
day before the next first pay day (with the days 20 and 5: the 20th to the 4th and the 5th to the 19th). The cut is on
the transaction's or the repayment's own ``date``, never on an instant, so it does not move with a time zone.
"""

import calendar
import datetime
from dataclasses import dataclass

from django.db.models import Case, IntegerField, Value, When
from django.db.models.expressions import Combinable

from common.enums import PaymentMode
from common.money import Currency
from common.pay_cycle import PayDays, cycle_of
from preferences import services as preferences
from salary.models import SalaryMonth
from users.models import User

FIRST_PART_LAST_DAY = 15  # where a month paid once is split; its parts are not shown
FIRST, SECOND = 1, 2


@dataclass(frozen=True)
class MonthPeriod:
    month: datetime.date  # the first day of the month the report is for
    first_day: datetime.date
    last_day: datetime.date
    second_start: datetime.date  # the first day of the second part

    def half(self, field: str = "date") -> Combinable:
        """An expression that is 1 for a day of the first part and 2 for a day of the second."""
        return Case(
            When(**{f"{field}__lt": self.second_start}, then=Value(FIRST)),
            default=Value(SECOND),
            output_field=IntegerField(),
        )


def period_of(month: datetime.date, pay_days: PayDays | None = None) -> MonthPeriod:
    """The days of the month whose first day is given: the pay cycle with ``pay_days``, else the calendar month."""
    if pay_days is not None:
        cycle = cycle_of(month, pay_days)
        return MonthPeriod(month, cycle.start, cycle.end, cycle.second_start)
    last = calendar.monthrange(month.year, month.month)[1]
    return MonthPeriod(
        month,
        first_day=month,
        last_day=month.replace(day=last),
        second_start=month.replace(day=FIRST_PART_LAST_DAY + 1),
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
