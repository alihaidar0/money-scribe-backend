"""The pay cycle of a month paid twice: which days belong to which payment (ADR 0023).

The first payment of month M is received on ``first_pay_day`` of M and the second on ``second_pay_day`` of M+1. What is
spent between two pay days comes out of the payment received at the start of that stretch, so with the days 20 and 5
the first part is the 20th to the 4th and the second part the 5th to the 19th. Everything here works on calendar days
and needs no database, so it is the one place the cut is decided.
"""

import datetime
from dataclasses import dataclass
from typing import Final

FIRST: Final = "first"
SECOND: Final = "second"


@dataclass(frozen=True)
class PayDays:
    first: int
    second: int

    @property
    def is_ordered(self) -> bool:
        """The cycle needs the second pay day before the first one."""
        return self.second < self.first


DEFAULT_PAY_DAYS: Final = PayDays(first=20, second=5)


@dataclass(frozen=True)
class Cycle:
    """The days of a month paid twice. ``end`` is the last day (inclusive); the second part starts at ``second_start``."""

    start: datetime.date
    second_start: datetime.date
    end: datetime.date


def shift(month: datetime.date, months: int) -> datetime.date:
    """The first day of the month ``months`` after (or before, when negative) the month of ``month``."""
    index = month.year * 12 + (month.month - 1) + months
    return datetime.date(index // 12, index % 12 + 1, 1)


def cycle_of(month: datetime.date, pay_days: PayDays) -> Cycle:
    """The cycle of the month whose first day is ``month``; the pay days must be ordered."""
    following = shift(month, 1)
    return Cycle(
        start=month.replace(day=pay_days.first),
        second_start=following.replace(day=pay_days.second),
        end=following.replace(day=pay_days.first) - datetime.timedelta(days=1),
    )


def nearest_pay_day(day: datetime.date, pay_days: PayDays) -> tuple[datetime.date, str]:
    """The part an income received on ``day`` joins when none was chosen: ``(month, part)``.

    It is the part whose pay day is nearest by calendar distance, in either direction; a tie goes to the later pay
    day. The second pay day of a month pays the second part of the month before.
    """
    here = day.replace(day=1)
    candidates: list[tuple[datetime.date, datetime.date, str]] = []
    for step in (-1, 0, 1):
        month = shift(here, step)
        candidates.append((month.replace(day=pay_days.first), month, FIRST))
        candidates.append(
            (month.replace(day=pay_days.second), shift(month, -1), SECOND)
        )
    _, month, part = min(
        candidates, key=lambda c: (abs((c[0] - day).days), -c[0].toordinal())
    )
    return month, part
