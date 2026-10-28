"""The pay cycle: the days of each part and the nearest pay day of an income (ADR 0023)."""

import datetime

import pytest

from common.pay_cycle import (
    DEFAULT_PAY_DAYS,
    FIRST,
    SECOND,
    PayDays,
    cycle_of,
    nearest_pay_day,
    shift,
)

D = datetime.date


def test_the_default_cycle_is_the_20th_to_the_4th_and_the_5th_to_the_19th() -> None:
    cycle = cycle_of(D(2026, 10, 1), DEFAULT_PAY_DAYS)

    assert (cycle.start, cycle.second_start, cycle.end) == (
        D(2026, 10, 20),
        D(2026, 11, 5),
        D(2026, 11, 19),
    )


def test_the_cycle_crosses_the_end_of_the_year_and_a_leap_february() -> None:
    december = cycle_of(D(2026, 12, 1), DEFAULT_PAY_DAYS)
    january = cycle_of(D(2028, 1, 1), PayDays(first=28, second=1))

    assert (december.second_start, december.end) == (D(2027, 1, 5), D(2027, 1, 19))
    assert (january.start, january.second_start, january.end) == (
        D(2028, 1, 28),
        D(2028, 2, 1),
        D(2028, 2, 27),
    )


def test_a_pair_is_ordered_when_the_second_day_is_before_the_first() -> None:
    assert PayDays(20, 5).is_ordered
    assert not PayDays(5, 5).is_ordered
    assert not PayDays(5, 20).is_ordered


@pytest.mark.parametrize(
    ("months", "expected"),
    [
        (1, D(2027, 1, 1)),
        (-1, D(2026, 11, 1)),
        (0, D(2026, 12, 1)),
        (13, D(2028, 1, 1)),
    ],
)
def test_shift_moves_whole_months(months: int, expected: datetime.date) -> None:
    assert shift(D(2026, 12, 17), months) == expected


@pytest.mark.parametrize(
    ("day", "month", "part"),
    [
        (D(2026, 10, 3), D(2026, 9, 1), SECOND),  # the 5th is two days away
        (D(2026, 10, 5), D(2026, 9, 1), SECOND),
        (
            D(2026, 10, 12),
            D(2026, 9, 1),
            SECOND,
        ),  # 7 days from the 5th, 8 from the 20th
        (D(2026, 10, 13), D(2026, 10, 1), FIRST),  # 8 from the 5th, 7 from the 20th
        (D(2026, 10, 20), D(2026, 10, 1), FIRST),
        (D(2026, 10, 31), D(2026, 10, 1), SECOND),  # 11 from the 20th, 5 from 5 Nov
        (D(2026, 11, 2), D(2026, 10, 1), SECOND),
        (D(2026, 12, 31), D(2026, 12, 1), SECOND),  # 5 from the 5th of January
        (D(2027, 1, 1), D(2026, 12, 1), SECOND),
    ],
)
def test_an_income_joins_the_part_whose_pay_day_is_nearest(
    day: datetime.date, month: datetime.date, part: str
) -> None:
    assert nearest_pay_day(day, DEFAULT_PAY_DAYS) == (month, part)


def test_a_tie_goes_to_the_later_pay_day() -> None:
    # With the days 10 and 2 the 6th is four days from both the 2nd and the 10th.
    assert nearest_pay_day(D(2026, 10, 6), PayDays(first=10, second=2)) == (
        D(2026, 10, 1),
        FIRST,
    )
