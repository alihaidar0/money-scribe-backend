"""The derived figures of a debt and the person's day, without HTTP."""

import datetime
from datetime import UTC
from datetime import datetime as moment

import pytest

from debts import presentation
from debts.models import Status
from debts.tests.factories import DebtFactory, RepaymentFactory
from users.tests.factories import VerifiedUserFactory

D = datetime.date


@pytest.mark.parametrize(
    ("repaid", "due", "today", "expected"),
    [
        (0, None, D(2026, 10, 15), Status.OPEN),
        (0, D(2026, 10, 15), D(2026, 10, 15), Status.OPEN),  # due today is not overdue
        (0, D(2026, 10, 16), D(2026, 10, 15), Status.OPEN),
        (0, D(2026, 10, 14), D(2026, 10, 15), Status.OVERDUE),
        (19999, D(2026, 10, 14), D(2026, 10, 15), Status.OVERDUE),  # a cent left
        (20000, D(2026, 10, 14), D(2026, 10, 15), Status.SETTLED),  # settled wins
        (20000, None, D(2026, 10, 15), Status.SETTLED),
        (25000, D(2026, 10, 14), D(2026, 10, 15), Status.SETTLED),
    ],
)
def test_the_status_of_a_debt(
    repaid: int, due: datetime.date | None, today: datetime.date, expected: Status
) -> None:
    assert (
        presentation.status_of(amount=20000, repaid=repaid, due_date=due, today=today)
        == expected
    )


@pytest.mark.django_db
def test_a_debt_is_presented_with_the_sum_of_its_repayments() -> None:
    debt = DebtFactory.create()
    RepaymentFactory.create(debt=debt, amount=5000, date=D(2026, 10, 21))
    RepaymentFactory.create(debt=debt, amount=2500, date=D(2026, 10, 20))

    shown = presentation.present_debt(debt, D(2026, 10, 15))

    assert shown.repaid.to_string() == "75.00"
    assert shown.remaining.to_string() == "125.00"
    assert shown.status == Status.OPEN
    assert [r.date for r in shown.repayments] == [D(2026, 10, 20), D(2026, 10, 21)]


@pytest.mark.django_db
def test_remaining_is_never_below_zero_even_for_data_that_overpays() -> None:
    debt = DebtFactory.create(amount=10000)
    RepaymentFactory.create(debt=debt, amount=7000)
    RepaymentFactory.create(debt=debt, amount=7000)  # the API cannot make this

    shown = presentation.present_debt(debt, D(2026, 10, 15))

    assert shown.remaining.to_string() == "0.00"
    assert shown.status == Status.SETTLED


@pytest.mark.django_db
def test_the_repayments_of_one_day_are_ordered_by_id() -> None:
    debt = DebtFactory.create()
    made = [RepaymentFactory.create(debt=debt, date=D(2026, 10, 20)) for _ in range(3)]

    shown = presentation.present_debt(debt, D(2026, 10, 15))

    assert [r.id for r in shown.repayments] == sorted(r.id for r in made)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("hour", "zone", "expected"),
    [
        (12, "Pacific/Kiritimati", D(2026, 10, 16)),  # UTC+14: already tomorrow
        (5, "Etc/GMT+12", D(2026, 10, 14)),  # UTC-12: still yesterday
        (5, "UTC", D(2026, 10, 15)),
        (23, "UTC", D(2026, 10, 15)),
    ],
)
def test_the_day_is_counted_where_the_person_is(
    monkeypatch: pytest.MonkeyPatch, hour: int, zone: str, expected: datetime.date
) -> None:
    from django.utils import timezone

    instant = moment(2026, 10, 15, hour, 0, tzinfo=UTC)
    monkeypatch.setattr(timezone, "now", lambda: instant)

    assert presentation.today_of(VerifiedUserFactory.create(time_zone=zone)) == expected


@pytest.mark.django_db
def test_a_zone_the_server_does_not_know_falls_back_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from django.utils import timezone

    instant = moment(2026, 10, 15, 23, 30, tzinfo=UTC)
    monkeypatch.setattr(timezone, "now", lambda: instant)
    user = VerifiedUserFactory.create(time_zone="Mars/Olympus")

    assert presentation.today_of(user) == D(2026, 10, 15)
