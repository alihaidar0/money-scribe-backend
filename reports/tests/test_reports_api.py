"""The reports and the Overview as HTTP calls: parameters, isolation, agreement between them, limits."""

import datetime
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from debts.models import Debt, Repayment
from debts.tests.factories import DebtFactory, RepaymentFactory
from ledger.models import Account, Category, Transaction
from reports.tests.builders import (
    DEBTS_REPORT,
    get,
    month_report_url,
    overview_url,
    set_up,
)
from salary.tests.helpers import add_item, month_body, put_month, read_month
from users.models import User
from users.tests.helpers import first_error

pytestmark = pytest.mark.django_db

ZERO = {
    "salary": "0.00",
    "recurring_income": "0.00",
    "fixed_costs": "0.00",
    "income_transactions": "0.00",
    "purchases": "0.00",
    "debt_repayments_received": "0.00",
    "debt_repayments_made": "0.00",
    "balance": "0.00",
}


@pytest.fixture(autouse=True)
def _today(clock: Any) -> None:
    """Every test runs on 2026-10-15."""


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


def spend(
    wallet: Account,
    amount: int,
    date: str = "2026-10-05",
    kind: str = "expense",
    currency: str = "RUB",
    category: str = "groceries",
) -> Transaction:
    return Transaction.objects.create(
        owner=wallet.owner,
        kind=kind,
        amount=amount,
        currency=currency,
        date=date,
        category=Category.objects.get(key=category),
        account=wallet,
    )


# --- the month and the currency ---------------------------------------------------------------


@pytest.mark.parametrize("url", [overview_url, month_report_url])
def test_an_empty_month_is_200_with_zeros(
    client: APIClient, me: tuple[User, str], url: Any
) -> None:
    response = get(client, url("2026-10"), me[1])

    assert response.status_code == 200
    body = response.json()
    assert (body["month"], body["currency"], body["has_data"]) == (
        "2026-10",
        "RUB",
        False,
    )
    assert body["currencies"] == []
    assert body["first_part"] is None and body["second_part"] is None


def test_an_empty_overview_has_every_line_at_zero(
    client: APIClient, me: tuple[User, str]
) -> None:
    body = get(client, overview_url("2026-10"), me[1]).json()

    assert body["total"] == ZERO


def test_an_empty_month_report_has_no_categories(
    client: APIClient, me: tuple[User, str]
) -> None:
    body = get(client, month_report_url("2026-10"), me[1]).json()

    assert (body["income_transactions"], body["purchases"], body["categories"]) == (
        "0.00",
        "0.00",
        [],
    )


@pytest.mark.parametrize("url", [overview_url, month_report_url])
@pytest.mark.parametrize("month", ["2000-01", "2099-12", "2024-02"])
def test_the_first_and_last_months_and_a_leap_february_are_routes(
    client: APIClient, me: tuple[User, str], url: Any, month: str
) -> None:
    assert get(client, url(month), me[1]).status_code == 200


@pytest.mark.parametrize("url", [overview_url, month_report_url])
@pytest.mark.parametrize(
    "month", ["1999-12", "2100-01", "2026-13", "2026-00", "2026-1", "26-10", "october"]
)
def test_a_month_key_that_does_not_fit_is_not_a_route(
    client: APIClient, me: tuple[User, str], url: Any, month: str
) -> None:
    response = get(client, url(month), me[1])

    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")  # the plain page, not JSON


@pytest.mark.parametrize("url", [overview_url, month_report_url])
def test_an_unsupported_currency_is_invalid_choice_on_currency(
    client: APIClient, me: tuple[User, str], url: Any
) -> None:
    response = get(client, url("2026-10"), me[1], currency="XXX")

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("invalid_choice", "currency")


@pytest.mark.parametrize("url", [overview_url, month_report_url])
def test_the_currency_left_out_is_the_settings_default_and_rub_until_set(
    client: APIClient, me: tuple[User, str], url: Any
) -> None:
    assert get(client, url("2026-10"), me[1]).json()["currency"] == "RUB"

    client.patch(
        "/api/v1/settings/",
        {"default_currency": "KWD"},
        headers={"Authorization": f"Bearer {me[1]}"},
    )

    assert get(client, url("2026-10"), me[1]).json()["currency"] == "KWD"
    assert (
        get(client, url("2026-10"), me[1], currency="USD").json()["currency"] == "USD"
    )


@pytest.mark.parametrize("url", [overview_url, month_report_url])
def test_only_get_is_offered(client: APIClient, me: tuple[User, str], url: Any) -> None:
    headers = {"Authorization": f"Bearer {me[1]}"}

    assert client.post(url("2026-10"), {}, headers=headers).status_code == 405
    assert client.put(url("2026-10"), {}, headers=headers).status_code == 405
    assert client.delete(url("2026-10"), headers=headers).status_code == 405


def test_every_report_needs_a_signed_in_person(client: APIClient) -> None:
    assert client.get(overview_url("2026-10")).status_code == 401
    assert client.get(month_report_url("2026-10")).status_code == 401
    assert client.get(DEBTS_REPORT).status_code == 401


def test_a_report_is_never_cached(client: APIClient, me: tuple[User, str]) -> None:
    for url in (overview_url("2026-10"), month_report_url("2026-10"), DEBTS_REPORT):
        assert "no-store" in get(client, url, me[1])["Cache-Control"]


# --- whose data ------------------------------------------------------------------------------


def test_another_persons_records_are_never_counted(
    client: APIClient,
    me: tuple[User, str],
    other: tuple[User, str],
    wallet: Account,
) -> None:
    spend(wallet, 1000)
    theirs = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    spend(theirs, 999_00)
    put_month(client, other[1])
    debt = DebtFactory.create(owner=other[0], currency="RUB")
    RepaymentFactory.create(debt=debt, date=datetime.date(2026, 10, 10))

    overview = get(client, overview_url("2026-10"), me[1]).json()
    report = get(client, month_report_url("2026-10"), me[1]).json()

    assert overview["total"] == {**ZERO, "purchases": "10.00", "balance": "-10.00"}
    assert report["purchases"] == "10.00"
    assert [c["amount"] for c in report["categories"]] == ["10.00"]
    assert get(client, DEBTS_REPORT, me[1]).json() == {
        "has_data": False,
        "by_currency": [],
    }


# --- the Overview agrees with the rest ---------------------------------------------------------


def test_salary_and_fixed_items_equal_the_balances_the_salary_month_returns(
    client: APIClient, me: tuple[User, str]
) -> None:
    put_month(client, me[1], "2026-10", month_body())
    for name, kind, amount, part in (
        ("Bonus", "income", "700.50", "first"),
        ("Rent", "expense", "3000.00", "first"),
        ("Phone", "expense", "250.25", "second"),
        ("Tutor", "income", "10.00", "second"),
    ):
        add_item(
            client,
            me[1],
            "2026-10",
            {"name": name, "kind": kind, "amount": amount, "part": part},
        )
    saved = read_month(client, me[1], "2026-10").json()["balances"]

    shown = get(client, overview_url("2026-10"), me[1]).json()

    for part, key in (
        ("first_part", "first"),
        ("second_part", "second"),
        ("total", "month"),
    ):
        block = shown[part]
        net = (
            _minor(block["salary"])
            + _minor(block["recurring_income"])
            - _minor(block["fixed_costs"])
        )
        assert net == _minor(saved[key]), part


def _minor(amount: str) -> int:
    whole, _, fraction = amount.partition(".")
    return int(whole + fraction)


def test_the_overview_of_a_one_payment_month_equals_its_balances_too(
    client: APIClient, me: tuple[User, str]
) -> None:
    put_month(
        client,
        me[1],
        "2026-10",
        month_body(payment_mode="one_payment", working_days_first_half=None),
    )
    add_item(
        client,
        me[1],
        "2026-10",
        {"name": "Rent", "kind": "expense", "amount": "3000.00", "part": "second"},
    )
    saved = read_month(client, me[1], "2026-10").json()["balances"]

    shown = get(client, overview_url("2026-10"), me[1]).json()["total"]

    assert saved["first"] == saved["month"]
    assert (_minor(shown["salary"]) - _minor(shown["fixed_costs"])) == _minor(
        saved["month"]
    )


@pytest.mark.parametrize("mode", ["one_payment", "two_payments"])
def test_the_month_report_equals_the_overviews_lines_of_the_same_name(
    client: APIClient, me: tuple[User, str], wallet: Account, mode: str
) -> None:
    client.patch(
        "/api/v1/settings/",
        {"payment_mode": mode},
        headers={"Authorization": f"Bearer {me[1]}"},
    )
    spend(wallet, 1234, "2026-10-01")
    spend(wallet, 777, "2026-10-15", category="dining")
    spend(wallet, 5000, "2026-10-16", kind="income", category="scholarship")
    spend(wallet, 99, "2026-10-31", category="dining")
    spend(wallet, 4321, "2026-10-20", currency="USD")

    overview = get(client, overview_url("2026-10"), me[1]).json()
    report = get(client, month_report_url("2026-10"), me[1]).json()

    for name in ("total", "first_part", "second_part"):
        block = overview[name]
        part = report if name == "total" else report[name]
        if block is None:
            assert part is None
            continue
        assert block["income_transactions"] == part["income_transactions"]
        assert block["purchases"] == part["purchases"]
        assert sum(_minor(c["amount"]) for c in part["categories"]) == _minor(
            part["purchases"]
        )


# --- has_data and currencies -------------------------------------------------------------------


def test_a_saved_salary_month_alone_is_data(
    client: APIClient, me: tuple[User, str]
) -> None:
    put_month(client, me[1], "2026-10", month_body())

    body = get(client, overview_url("2026-10"), me[1]).json()

    assert (body["has_data"], body["currencies"]) == (True, ["RUB"])
    assert get(client, month_report_url("2026-10"), me[1]).json()["has_data"] is False


def test_a_repayment_alone_is_data_in_the_overview_but_not_in_the_month_report(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = DebtFactory.create(owner=me[0], currency="EUR")
    RepaymentFactory.create(debt=debt, date=datetime.date(2026, 10, 10))

    overview = get(client, overview_url("2026-10"), me[1]).json()
    report = get(client, month_report_url("2026-10"), me[1]).json()

    assert (overview["has_data"], overview["currencies"]) == (True, ["EUR"])
    assert (report["has_data"], report["currencies"]) == (False, [])


def test_a_debt_that_is_only_created_is_not_a_movement(
    client: APIClient, me: tuple[User, str]
) -> None:
    DebtFactory.create(owner=me[0], start_date=datetime.date(2026, 10, 1))

    body = get(client, overview_url("2026-10"), me[1]).json()

    assert body["has_data"] is False
    assert body["total"] == ZERO


def test_the_currencies_are_ordered_by_code_without_repeats(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for currency in ("USD", "EUR", "USD", "RUB", "KWD"):
        spend(wallet, 100, currency=currency)

    body = get(client, month_report_url("2026-10"), me[1]).json()

    assert body["currencies"] == ["EUR", "KWD", "RUB", "USD"]


def test_a_sum_past_the_largest_amount_is_400_invalid_amount(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for day in ("2026-10-01", "2026-10-02"):
        spend(wallet, 999_999_999_999_999, day)

    for url in (overview_url("2026-10"), month_report_url("2026-10")):
        response = get(client, url, me[1])

        assert response.status_code == 400
        assert first_error(response)["code"] == "invalid_amount"


def test_income_and_spending_that_cancel_give_a_balance_of_0_00_not_minus_zero(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, 500)
    spend(wallet, 500, kind="income", category="scholarship")

    total = get(client, overview_url("2026-10"), me[1]).json()["total"]

    assert total["balance"] == "0.00"


# --- limits and cost ---------------------------------------------------------------------------


def test_the_reports_count_in_the_reports_group_only(
    client: APIClient, me: tuple[User, str], settings: Any
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "reports": "3/min",
            "reads": "1/min",
        },
    }
    urls = [overview_url("2026-10"), month_report_url("2026-10"), DEBTS_REPORT]

    codes = [get(client, url, me[1]).status_code for url in urls * 2]

    assert codes == [200, 200, 200, 429, 429, 429]
    refused = get(client, urls[0], me[1])
    assert 1 <= int(refused["Retry-After"]) <= 60


@pytest.mark.parametrize("url", [overview_url("2026-10"), month_report_url("2026-10")])
def test_the_number_of_queries_does_not_grow_with_the_records(
    client: APIClient, me: tuple[User, str], wallet: Account, url: str
) -> None:
    def count() -> int:
        from django.core.cache import cache

        cache.clear()
        with CaptureQueriesContext(connection) as queries:
            assert get(client, url, me[1]).status_code == 200
        return len(queries)

    spend(wallet, 100)
    few = count()
    for day in range(1, 29):
        spend(wallet, 100 + day, f"2026-10-{day:02d}", category="dining")
        debt = DebtFactory.create(owner=me[0], currency="RUB")
        RepaymentFactory.create(debt=debt, date=datetime.date(2026, 10, day))

    assert count() == few


# --- the debts report --------------------------------------------------------------------------


def test_the_debts_report_counts_the_day_in_the_persons_time_zone(
    client: APIClient, me: tuple[User, str], clock: Any
) -> None:
    from datetime import UTC

    clock.current = datetime.datetime(2026, 10, 15, 5, 0, tzinfo=UTC)
    DebtFactory.create(owner=me[0], due_date=datetime.date(2026, 10, 14))

    in_utc = get(client, DEBTS_REPORT, me[1]).json()["by_currency"][0]
    User.objects.filter(pk=me[0].pk).update(time_zone="Etc/GMT+12")  # still the 14th
    behind = get(client, DEBTS_REPORT, me[1]).json()["by_currency"][0]

    assert (len(in_utc["overdue"]), len(in_utc["open"])) == (1, 0)
    assert (len(behind["overdue"]), len(behind["open"])) == (0, 1)


@pytest.mark.parametrize(
    ("today", "code"),
    [
        ("tomorrow", "invalid"),
        ("2026-02-30", "invalid"),
        ("2100-01-01", "date_out_of_range"),
    ],
)
def test_a_bad_today_is_a_400_on_today(
    client: APIClient, me: tuple[User, str], today: str, code: str
) -> None:
    response = get(client, DEBTS_REPORT, me[1], today=today)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, "today")


def test_debts_with_the_same_due_day_are_ordered_by_id(
    client: APIClient, me: tuple[User, str]
) -> None:
    made = [
        DebtFactory.create(owner=me[0], due_date=datetime.date(2026, 10, 1))
        for _ in range(3)
    ] + [DebtFactory.create(owner=me[0], due_date=None) for _ in range(3)]
    expected_overdue = sorted(str(d.id) for d in made[:3])
    expected_open = sorted(str(d.id) for d in made[3:])

    block = get(client, DEBTS_REPORT, me[1]).json()["by_currency"][0]

    assert [d["id"] for d in block["overdue"]] == expected_overdue
    assert [d["id"] for d in block["open"]] == expected_open


def test_the_debts_report_gives_the_remaining_not_the_amount(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = DebtFactory.create(owner=me[0], amount=20000)
    RepaymentFactory.create(debt=debt, amount=5000)
    RepaymentFactory.create(debt=debt, amount=2500)

    block = get(client, DEBTS_REPORT, me[1]).json()["by_currency"][0]

    assert block["owed_to_you"] == "125.00"
    assert block["open"][0]["remaining"] == "125.00"


def test_the_debts_report_queries_do_not_grow_with_the_debts(
    client: APIClient, me: tuple[User, str]
) -> None:
    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert get(client, DEBTS_REPORT, me[1]).status_code == 200
        return len(queries)

    RepaymentFactory.create_batch(2, debt=DebtFactory.create(owner=me[0]))
    few = count()
    for _ in range(10):
        RepaymentFactory.create_batch(3, debt=DebtFactory.create(owner=me[0]))

    assert count() == few
    assert Debt.objects.count() == 11 and Repayment.objects.count() == 32


def test_set_up_builds_a_whole_person_for_the_vectors(
    client: APIClient, me: tuple[User, str]
) -> None:
    custom = set_up(
        client,
        me,
        {
            "custom_categories": [{"name": "Pets", "icon": "gifts"}],
            "transactions": [
                {
                    "kind": "expense",
                    "amount": "1.00",
                    "currency": "RUB",
                    "date": "2026-10-01",
                    "category": "custom:Pets",
                }
            ],
        },
    )

    assert set(custom) == {"Pets"}
    assert Transaction.objects.get().category_id is not None
