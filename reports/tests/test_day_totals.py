"""The day totals (ADR 0003): one item per day with its currencies, in step with the transaction list."""

from decimal import Decimal
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from ledger.models import Account, Category, Transaction
from ledger.tests.helpers import DAY_TOTALS, TRANSACTIONS, get
from users.models import User
from users.tests.helpers import bearer, first_error

pytestmark = pytest.mark.django_db


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


def make(
    account: Account,
    date: str,
    amount: int,
    kind: str = "expense",
    currency: str = "USD",
    category_key: str = "other",
) -> Transaction:
    return Transaction.objects.create(
        owner=account.owner,
        kind=kind,
        amount=amount,
        currency=currency,
        date=date,
        category=Category.objects.get(key=category_key),
        account=account,
        note="",
    )


def days(response: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = response.json()["results"]
    return results


def walk(client: APIClient, token: str, **params: Any) -> list[list[dict[str, Any]]]:
    pages = []
    response = get(client, DAY_TOTALS, token, **params)
    while True:
        assert response.status_code == 200, response.json()
        pages.append(days(response))
        link = response.json()["next"]
        if link is None:
            return pages
        response = client.get(link, headers=bearer(token))


# --- what an item is --------------------------------------------------------------------------


def test_a_day_holds_its_currencies_by_code_with_income_minus_expenses(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-15", 5000, "income", "USD")
    make(wallet, "2026-09-15", 800, "expense", "USD")
    make(wallet, "2026-09-15", 4000, "income", "USD")
    make(wallet, "2026-09-15", 1250, "expense", "KWD")
    make(wallet, "2026-09-14", 800, "expense", "USD")

    response = get(client, DAY_TOTALS, me[1])

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.json() == {
        "next": None,
        "previous": None,
        "results": [
            {
                "date": "2026-09-15",
                "totals": [
                    {"currency": "KWD", "total": "-1.250"},
                    {"currency": "USD", "total": "82.00"},
                ],
            },
            {"date": "2026-09-14", "totals": [{"currency": "USD", "total": "-8.00"}]},
        ],
    }


def test_each_currency_has_its_own_digits(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-15", 1200, "expense", "JPY")
    make(wallet, "2026-09-15", 12500, "income", "KWD")
    make(wallet, "2026-09-15", 1, "expense", "EUR")

    totals = days(get(client, DAY_TOTALS, me[1]))[0]["totals"]

    assert totals == [
        {"currency": "EUR", "total": "-0.01"},
        {"currency": "JPY", "total": "-1200"},
        {"currency": "KWD", "total": "12.500"},
    ]


def test_a_day_that_nets_to_zero_is_zero_never_minus_zero(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-15", 1000, "income")
    make(wallet, "2026-09-15", 1000, "expense")

    totals = days(get(client, DAY_TOTALS, me[1]))[0]["totals"]

    assert totals == [{"currency": "USD", "total": "0.00"}]


def test_the_sums_are_exact_to_the_minor_unit(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for _ in range(1000):
        make(wallet, "2026-09-15", 1, "expense")
    make(wallet, "2026-09-15", 999_999_999_999, "income")

    totals = days(get(client, DAY_TOTALS, me[1]))[0]["totals"]

    assert totals == [{"currency": "USD", "total": "9999999989.99"}]


def test_a_day_with_no_transaction_has_no_item_and_no_data_is_an_empty_page(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-01", 100)
    make(wallet, "2026-09-05", 100)

    dates = [d["date"] for d in days(get(client, DAY_TOTALS, me[1]))]
    nothing = get(client, DAY_TOTALS, me[1], date_from="2030-01-01")

    assert dates == ["2026-09-05", "2026-09-01"]
    assert nothing.status_code == 200
    assert nothing.json() == {"next": None, "previous": None, "results": []}


def test_a_total_past_the_largest_amount_is_400_invalid_amount(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-15", 999_999_999_999_999, "income")
    make(wallet, "2026-09-15", 999_999_999_999_999, "income")

    response = get(client, DAY_TOTALS, me[1])

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_amount"


# --- pages of days ----------------------------------------------------------------------------


def test_the_limit_counts_days_and_a_day_is_never_split(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for day in ("2026-09-10", "2026-09-11", "2026-09-12"):
        for currency in ("USD", "EUR", "KWD", "JPY"):
            make(wallet, day, 100, "expense", currency)

    pages = walk(client, me[1], limit=2)

    assert [[d["date"] for d in page] for page in pages] == [
        ["2026-09-12", "2026-09-11"],
        ["2026-09-10"],
    ]
    assert all(len(d["totals"]) == 4 for page in pages for d in page)


def test_the_first_page_starts_on_the_same_day_as_the_list_and_both_follow_the_same_order(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for day in ("2026-08-01", "2026-09-03", "2026-09-03", "2026-07-30", "2026-10-02"):
        make(wallet, day, 100)

    totals = [d["date"] for d in days(get(client, DAY_TOTALS, me[1]))]
    listed = [i["date"] for i in get(client, TRANSACTIONS, me[1]).json()["results"]]

    assert totals == sorted(set(listed), reverse=True)
    assert totals[0] == listed[0]


def test_the_totals_equal_the_sums_of_the_list_for_every_filter(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    purse = Account.objects.create(owner=me[0], name="Purse", kind="cash")
    for index in range(30):
        make(
            wallet if index % 3 else purse,
            f"2026-09-{1 + index % 9:02d}",
            100 + index * 7,
            "income" if index % 4 == 0 else "expense",
            ("USD", "EUR")[index % 2],
            "other",
        )

    for params in (
        {},
        {"account_id": str(purse.id)},
        {"kind": "expense"},
        {"date_from": "2026-09-04", "date_to": "2026-09-07"},
    ):
        sums: dict[tuple[str, str], int] = {}
        page = get(client, TRANSACTIONS, me[1], limit=100, **params)
        for item in page.json()["results"]:
            minor = int(Decimal(item["amount"]) * 100)
            key = (item["date"], item["currency"])
            sums[key] = sums.get(key, 0) + (
                minor if item["kind"] == "income" else -minor
            )
        reported = {
            (d["date"], t["currency"]): int(Decimal(t["total"]) * 100)
            for d in days(get(client, DAY_TOTALS, me[1], limit=100, **params))
            for t in d["totals"]
        }

        assert reported == sums, params


def test_the_pages_can_be_walked_back_with_previous(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for day in range(1, 8):
        make(wallet, f"2026-09-{day:02d}", 100)

    first = get(client, DAY_TOTALS, me[1], limit=3)
    second = client.get(first.json()["next"], headers=bearer(me[1]))
    third = client.get(second.json()["next"], headers=bearer(me[1]))
    back = client.get(third.json()["previous"], headers=bearer(me[1]))
    back_again = client.get(back.json()["previous"], headers=bearer(me[1]))

    assert first.json()["previous"] is None
    assert [d["date"] for d in days(second)] == [
        "2026-09-04",
        "2026-09-03",
        "2026-09-02",
    ]
    assert [d["date"] for d in days(third)] == ["2026-09-01"]
    assert [d["date"] for d in days(back)] == [d["date"] for d in days(second)]
    assert [d["date"] for d in days(back_again)] == [d["date"] for d in days(first)]
    assert back_again.json()["previous"] is None


def test_a_mistyped_date_far_away_costs_no_extra_call(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    """The reason the span is not capped: one record in 2099 and the rest in 2026 are still two pages of days."""
    make(wallet, "2099-01-01", 100)
    for day in range(1, 6):
        make(wallet, f"2026-09-{day:02d}", 100)

    page = get(client, DAY_TOTALS, me[1], limit=2)

    assert [d["date"] for d in days(page)] == ["2099-01-01", "2026-09-05"]
    assert page.json()["next"] is not None
    assert (
        get(
            client, DAY_TOTALS, me[1], date_from="2000-01-01", date_to="2099-12-31"
        ).status_code
        == 200
    )


def test_a_hundred_days_per_page_is_the_most_and_fifty_the_default(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    Transaction.objects.bulk_create(
        [
            Transaction(
                owner=me[0],
                kind="expense",
                amount=100,
                currency="USD",
                date=f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}",
                category=Category.objects.get(key="other"),
                account=wallet,
            )
            for n in range(120)
        ]
    )

    assert len(days(get(client, DAY_TOTALS, me[1]))) == 50
    assert len(days(get(client, DAY_TOTALS, me[1], limit=1000))) == 100
    assert first_error(get(client, DAY_TOTALS, me[1], limit=0))["attr"] == "limit"
    assert (
        first_error(get(client, DAY_TOTALS, me[1], cursor="nonsense"))["attr"]
        == "cursor"
    )


# --- the filters ------------------------------------------------------------------------------


def test_the_day_filters_apply(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    purse = Account.objects.create(owner=me[0], name="Purse", kind="cash")
    make(wallet, "2026-09-01", 100)
    make(purse, "2026-09-02", 100, "income")
    make(purse, "2026-09-03", 100)

    by_account = days(get(client, DAY_TOTALS, me[1], account_id=str(purse.id)))
    by_kind = days(get(client, DAY_TOTALS, me[1], kind="income"))
    ranged = days(
        get(client, DAY_TOTALS, me[1], date_from="2026-09-02", date_to="2026-09-02")
    )
    by_category = days(
        get(
            client,
            DAY_TOTALS,
            me[1],
            category_id="0198f2a0-0000-7000-8000-000000000007",
        )
    )

    assert [d["date"] for d in by_account] == ["2026-09-03", "2026-09-02"]
    assert [d["date"] for d in by_kind] == ["2026-09-02"]
    assert [d["date"] for d in ranged] == ["2026-09-02"]
    assert len(by_category) == 3


@pytest.mark.parametrize(
    ("params", "code", "attr"),
    [
        ({"date_from": "2026-13-01"}, "invalid", "date_from"),
        (
            {"date_from": "2026-10-31", "date_to": "2026-10-01"},
            "invalid_date_range",
            "date_to",
        ),
        ({"kind": "transfer"}, "invalid_choice", "kind"),
        ({"account_id": "x"}, "invalid", "account_id"),
    ],
)
def test_a_bad_filter_is_a_400_on_its_parameter(
    client: APIClient,
    me: tuple[User, str],
    params: dict[str, str],
    code: str,
    attr: str,
) -> None:
    response = get(client, DAY_TOTALS, me[1], **params)

    error = first_error(response)
    assert (response.status_code, error["code"], error["attr"]) == (400, code, attr)


def test_search_is_not_a_filter_of_the_totals_and_is_ignored(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-01", 100)

    plain = get(client, DAY_TOTALS, me[1])
    searched = get(client, DAY_TOTALS, me[1], search="nothing matches this")
    invalid = get(client, DAY_TOTALS, me[1], search="x")

    assert searched.json() == plain.json()
    assert invalid.status_code == 200  # not even validated: it is not a parameter here


# --- ownership and access ---------------------------------------------------------------------


def test_the_totals_cover_only_the_persons_own_transactions(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-01", 100)
    theirs = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    make(theirs, "2026-09-01", 99999)
    make(theirs, "2026-09-02", 5)

    mine = days(get(client, DAY_TOTALS, me[1]))
    filtered = days(get(client, DAY_TOTALS, me[1], account_id=str(theirs.id)))

    assert mine == [
        {"date": "2026-09-01", "totals": [{"currency": "USD", "total": "-1.00"}]}
    ]
    assert filtered == []


def test_it_needs_a_signed_in_person(client: APIClient) -> None:
    assert client.get(DAY_TOTALS).status_code == 401


def test_the_cost_does_not_grow_with_the_number_of_days_on_a_page(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-01", 100)
    with CaptureQueriesContext(connection) as small:
        get(client, DAY_TOTALS, me[1])
    for day in range(2, 28):
        make(wallet, f"2026-09-{day:02d}", 100)
    with CaptureQueriesContext(connection) as big:
        get(client, DAY_TOTALS, me[1])

    assert len(big) == len(small)


def test_the_totals_have_their_own_rate_group_and_the_reads_are_untouched(
    client: APIClient, me: tuple[User, str], settings: Settings
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "reports": "2/min",
            "reads": "1/min",
        },
    }

    totals = [get(client, DAY_TOTALS, me[1]).status_code for _ in range(3)]
    read = get(client, TRANSACTIONS, me[1]).status_code

    assert totals == [200, 200, 429]
    assert read == 200
