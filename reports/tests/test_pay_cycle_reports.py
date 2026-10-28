"""The Overview and the month report cut by the pay cycle, and an income's part (ADR 0023)."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from ledger.models import Account, Transaction
from reports.tests.builders import get, month_report_url, overview_url
from reports.tests.test_reports_api import spend
from users.models import User
from users.tests.helpers import bearer

pytestmark = pytest.mark.django_db

SETTINGS = "/api/v1/settings/"


@pytest.fixture(autouse=True)
def _today(clock: Any) -> None:
    """Every test runs on 2026-10-15."""


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


def two_payments(client: APIClient, token: str, **days: int) -> None:
    body = {"payment_mode": "two_payments", **days}
    assert client.patch(SETTINGS, body, headers=bearer(token)).status_code == 200


def lines(
    client: APIClient, token: str, month: str = "2026-10"
) -> dict[str, dict[str, str]]:
    body = get(client, overview_url(month), token).json()
    return {name: body[name] for name in ("total", "first_part", "second_part")}


def purchases(client: APIClient, token: str, month: str = "2026-10") -> list[str]:
    body = lines(client, token, month)
    return [body[name]["purchases"] for name in ("total", "first_part", "second_part")]


def test_expenses_are_cut_by_the_pay_cycle(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    two_payments(client, me[1])
    for amount, day in [
        (1, "2026-10-19"),  # the month before's second part
        (2, "2026-10-20"),
        (4, "2026-11-04"),
        (8, "2026-11-05"),
        (16, "2026-11-19"),
        (32, "2026-11-20"),  # the next month's first part
    ]:
        spend(wallet, amount * 100, day)

    assert purchases(client, me[1]) == ["30.00", "6.00", "24.00"]


def test_the_cycle_follows_other_pay_days(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    two_payments(client, me[1], first_pay_day=25, second_pay_day=10)
    for amount, day in [
        (1, "2026-10-24"),
        (2, "2026-10-25"),
        (4, "2026-11-09"),
        (8, "2026-11-10"),
        (16, "2026-11-24"),
        (32, "2026-11-25"),
    ]:
        spend(wallet, amount * 100, day)

    assert purchases(client, me[1]) == ["30.00", "6.00", "24.00"]


def test_a_month_paid_once_stays_the_calendar_month(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, 100, "2026-09-30")
    spend(wallet, 200, "2026-10-01")
    spend(wallet, 400, "2026-10-31")
    spend(wallet, 800, "2026-11-01")

    body = get(client, overview_url("2026-10"), me[1]).json()

    assert body["total"]["purchases"] == "6.00"
    assert body["first_part"] is None


def test_an_income_joins_the_nearest_pay_day_wherever_its_date_is(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    two_payments(client, me[1])
    spend(
        wallet, 1000, "2026-10-03", kind="income"
    )  # paid with the 5th: September's second part
    spend(
        wallet, 2000, "2026-10-18", kind="income"
    )  # paid with the 20th: October's first part
    spend(
        wallet, 4000, "2026-11-03", kind="income"
    )  # paid with 5 Nov: October's second part

    october = lines(client, me[1], "2026-10")
    september = lines(client, me[1], "2026-09")

    assert [
        october[n]["income_transactions"]
        for n in ("total", "first_part", "second_part")
    ] == ["60.00", "20.00", "40.00"]
    assert [
        september[n]["income_transactions"]
        for n in ("total", "first_part", "second_part")
    ] == ["10.00", "0.00", "10.00"]


def test_a_chosen_part_wins_over_the_date(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    two_payments(client, me[1])
    income = spend(wallet, 5000, "2026-10-21", kind="income")
    Transaction.objects.filter(pk=income.pk).update(
        part="second", part_month="2026-12-01"
    )

    october = lines(client, me[1], "2026-10")
    december = lines(client, me[1], "2026-12")

    assert october["total"]["income_transactions"] == "0.00"
    assert december["second_part"]["income_transactions"] == "50.00"
    assert december["first_part"]["income_transactions"] == "0.00"
    assert december["total"]["income_transactions"] == "50.00"


def test_a_part_is_ignored_by_a_month_paid_once(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    income = spend(wallet, 5000, "2026-10-21", kind="income")
    Transaction.objects.filter(pk=income.pk).update(
        part="second", part_month="2026-12-01"
    )

    body = get(client, overview_url("2026-10"), me[1]).json()

    assert body["total"]["income_transactions"] == "50.00"
    assert (
        get(client, overview_url("2026-12"), me[1]).json()["total"][
            "income_transactions"
        ]
        == "0.00"
    )


def test_changing_the_pay_days_re_cuts_the_history(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    two_payments(client, me[1])
    spend(wallet, 100, "2026-10-15")
    spend(
        wallet, 5000, "2026-10-08", kind="income"
    )  # 3 days from the 5th: September's second part
    before = lines(client, me[1])
    assert before["total"]["purchases"] == "0.00"  # the 15th is in September's cycle
    assert before["total"]["income_transactions"] == "0.00"

    two_payments(client, me[1], first_pay_day=10, second_pay_day=2)

    after = lines(client, me[1])
    assert (
        after["first_part"]["purchases"] == "1.00"
    )  # October's cycle now starts on the 10th
    assert (
        after["first_part"]["income_transactions"] == "50.00"
    )  # and the 8th is paid with the 10th


def test_the_month_report_uses_the_same_cut(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    two_payments(client, me[1])
    spend(wallet, 100, "2026-10-19")
    spend(wallet, 200, "2026-10-20")
    spend(wallet, 400, "2026-11-05")
    spend(
        wallet, 800, "2026-11-04", kind="income", category="scholarship"
    )  # paid with the 5th

    body = get(client, month_report_url("2026-10"), me[1]).json()

    assert (body["purchases"], body["income_transactions"]) == ("6.00", "8.00")
    assert body["first_part"]["purchases"] == "2.00"
    assert body["second_part"]["purchases"] == "4.00"
    assert body["first_part"]["income_transactions"] == "0.00"
    assert body["second_part"]["income_transactions"] == "8.00"
    assert body["has_data"] is True


def test_an_income_paid_in_another_month_still_makes_the_month_have_data(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    two_payments(client, me[1])
    spend(
        wallet, 800, "2026-10-18", kind="income"
    )  # dated in September's cycle, paid with 20 Oct

    body = get(client, overview_url("2026-10"), me[1]).json()

    assert (body["has_data"], body["currencies"]) == (True, ["RUB"])


def test_other_peoples_incomes_are_not_counted(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    two_payments(client, me[1])
    theirs = Account.objects.create(owner=other[0], name="Wallet", kind="cash")
    income = spend(theirs, 800, "2026-10-21", kind="income")
    Transaction.objects.filter(pk=income.pk).update(
        part="first", part_month="2026-10-01"
    )

    assert lines(client, me[1])["total"]["income_transactions"] == "0.00"
