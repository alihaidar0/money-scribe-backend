"""The transaction list: its order, its pages and its filters (ADR 0003)."""

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from ledger.models import Account, Category, Transaction
from ledger.tests.helpers import TRANSACTIONS, get
from users.models import User
from users.tests.helpers import bearer, first_error

pytestmark = pytest.mark.django_db


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


def make(account: Account, date: str, note: str = "", **fields: Any) -> Transaction:
    values: dict[str, Any] = {
        "owner": account.owner,
        "kind": "expense",
        "amount": 1000,
        "currency": "USD",
        "date": date,
        "category": Category.objects.get(key="groceries"),
        "account": account,
        "note": note,
    }
    return Transaction.objects.create(**{**values, **fields})


def listed(response: Any) -> list[str]:
    return [item["id"] for item in response.json()["results"]]


def walk(client: APIClient, token: str, **params: Any) -> list[list[str]]:
    pages = []
    response = get(client, TRANSACTIONS, token, **params)
    while True:
        assert response.status_code == 200, response.json()
        pages.append(listed(response))
        link = response.json()["next"]
        if link is None:
            return pages
        response = client.get(link, headers=bearer(token))


# --- the order and the pages ------------------------------------------------------------------


def test_the_newest_day_comes_first_and_within_a_day_the_entry_added_last(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    old = make(wallet, "2026-10-01")
    first_today = make(wallet, "2026-10-03")
    last_today = make(wallet, "2026-10-03")
    middle = make(wallet, "2026-10-02")

    response = get(client, TRANSACTIONS, me[1])

    assert response.status_code == 200
    assert listed(response) == [
        str(t.id) for t in (last_today, first_today, middle, old)
    ]


def test_the_order_does_not_depend_on_when_the_record_was_dated_relative_to_added(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    """A transaction added today for an old day sits with that day, not at the top."""
    added_first = make(wallet, "2026-10-20")
    added_last_for_an_old_day = make(wallet, "2026-09-01")

    assert listed(get(client, TRANSACTIONS, me[1])) == [
        str(added_first.id),
        str(added_last_for_an_old_day.id),
    ]


def test_pages_have_no_gap_and_no_repeat_even_inside_one_day(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    made = [make(wallet, "2026-10-05") for _ in range(5)]
    made += [make(wallet, "2026-10-04") for _ in range(4)]
    expected = [
        str(t.id)
        for t in sorted(made, key=lambda t: (t.date, t.created_at, t.id), reverse=True)
    ]

    pages = walk(client, me[1], limit=3)

    assert [len(p) for p in pages] == [3, 3, 3]
    assert [i for page in pages for i in page] == expected


def test_a_hundred_per_page_is_the_most_and_fifty_the_default(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    Transaction.objects.bulk_create(
        [
            Transaction(
                owner=me[0],
                kind="expense",
                amount=100,
                currency="USD",
                date="2026-10-01",
                category=Category.objects.get(key="other"),
                account=wallet,
                note=f"n{index}",
            )
            for index in range(120)
        ]
    )

    default = get(client, TRANSACTIONS, me[1])
    biggest = get(client, TRANSACTIONS, me[1], limit=1000)

    assert len(default.json()["results"]) == 50
    assert len(biggest.json()["results"]) == 100
    assert first_error(get(client, TRANSACTIONS, me[1], limit=0))["attr"] == "limit"


def test_the_list_holds_only_the_persons_own_transactions(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], wallet: Account
) -> None:
    mine = make(wallet, "2026-10-01")
    theirs_account = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    make(theirs_account, "2026-10-01")

    assert listed(get(client, TRANSACTIONS, me[1])) == [str(mine.id)]


def test_an_empty_list_is_200(client: APIClient, me: tuple[User, str]) -> None:
    response = get(client, TRANSACTIONS, me[1])

    assert response.status_code == 200
    assert response.json() == {"next": None, "previous": None, "results": []}


def test_a_page_costs_the_same_number_of_queries_whatever_it_holds(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-10-01")
    make(wallet, "2026-10-02")
    with CaptureQueriesContext(connection) as small:
        get(client, TRANSACTIONS, me[1])
    for index in range(40):
        make(wallet, "2026-10-03", note=f"n{index}")
    with CaptureQueriesContext(connection) as big:
        get(client, TRANSACTIONS, me[1])

    assert len(big) == len(small)


# --- the filters ------------------------------------------------------------------------------


def test_the_days_are_inclusive_at_both_ends(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for day in ("2026-09-30", "2026-10-01", "2026-10-15", "2026-10-31", "2026-11-01"):
        make(wallet, day, note=day)

    response = get(
        client, TRANSACTIONS, me[1], date_from="2026-10-01", date_to="2026-10-31"
    )

    assert [i["date"] for i in response.json()["results"]] == [
        "2026-10-31",
        "2026-10-15",
        "2026-10-01",
    ]


def test_one_end_alone_is_enough(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for day in ("2026-09-30", "2026-10-15"):
        make(wallet, day)

    after = get(client, TRANSACTIONS, me[1], date_from="2026-10-01")
    before = get(client, TRANSACTIONS, me[1], date_to="2026-10-01")

    assert [i["date"] for i in after.json()["results"]] == ["2026-10-15"]
    assert [i["date"] for i in before.json()["results"]] == ["2026-09-30"]


def test_a_category_and_an_account_filter_repeat(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    purse = Account.objects.create(owner=me[0], name="Purse", kind="cash")
    dining = Category.objects.get(key="dining")
    groceries = Category.objects.get(key="groceries")
    transport = Category.objects.get(key="transport")
    a = make(wallet, "2026-10-01", category=groceries)
    b = make(wallet, "2026-10-02", category=dining)
    c = make(purse, "2026-10-03", category=transport)

    one = get(client, TRANSACTIONS, me[1], category_id=str(dining.id))
    two = client.get(
        f"{TRANSACTIONS}?category_id={groceries.id}&category_id={dining.id}",
        headers=bearer(me[1]),
    )
    by_account = get(client, TRANSACTIONS, me[1], account_id=str(purse.id))
    both = client.get(
        f"{TRANSACTIONS}?account_id={purse.id}&category_id={groceries.id}",
        headers=bearer(me[1]),
    )

    assert listed(one) == [str(b.id)]
    assert set(listed(two)) == {str(a.id), str(b.id)}
    assert listed(by_account) == [str(c.id)]
    assert listed(both) == []


def test_the_kind_filters(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spent = make(wallet, "2026-10-01")
    earned = make(
        wallet, "2026-10-02", kind="income", category=Category.objects.get(key="other")
    )

    assert listed(get(client, TRANSACTIONS, me[1], kind="expense")) == [str(spent.id)]
    assert listed(get(client, TRANSACTIONS, me[1], kind="income")) == [str(earned.id)]


def test_the_filters_combine(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-09-01", note="Weekly shop")
    hit = make(wallet, "2026-10-05", note="Weekly shop")
    make(wallet, "2026-10-06", note="Lunch")

    response = get(
        client,
        TRANSACTIONS,
        me[1],
        date_from="2026-10-01",
        search="shop",
        kind="expense",
    )

    assert listed(response) == [str(hit.id)]


def test_an_id_that_matches_nothing_is_an_empty_page_not_an_error(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-10-01")
    theirs = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    make(theirs, "2026-10-01")

    unknown = get(
        client, TRANSACTIONS, me[1], account_id="0198f2a4-7c3e-7b1d-9a52-3e6f1d0c4b21"
    )
    foreign = get(client, TRANSACTIONS, me[1], account_id=str(theirs.id))

    assert (unknown.status_code, foreign.status_code) == (200, 200)
    assert unknown.json()["results"] == foreign.json()["results"] == []


def test_filters_never_show_another_persons_rows(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], wallet: Account
) -> None:
    theirs = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    make(theirs, "2026-10-01", note="secret lunch")

    assert listed(get(client, TRANSACTIONS, me[1], search="secret")) == []


# --- search -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("shop", ["Weekly shop", "SHOPPING"]),
        ("SHOP", ["Weekly shop", "SHOPPING"]),
        ("eekly", ["Weekly shop"]),
        ("0%", ["50% off"]),
        ("a_", ["a_b"]),
        ("%%", []),
        ("حليب", ["حليب طازج"]),
        ("café", ["Café stop"]),
        ("Café", ["Café stop"]),
    ],
)
def test_search_is_a_case_insensitive_substring_of_the_note_with_literal_wildcards(
    client: APIClient,
    me: tuple[User, str],
    wallet: Account,
    term: str,
    expected: list[str],
) -> None:
    for note in [
        "Weekly shop",
        "SHOPPING",
        "50% off",
        "a_b",
        "axb",
        "حليب طازج",
        "Café stop",
        "Bus",
    ]:
        make(wallet, "2026-10-01", note=note)

    response = get(client, TRANSACTIONS, me[1], search=term)

    assert response.status_code == 200
    assert sorted(i["note"] for i in response.json()["results"]) == sorted(expected)


def test_search_looks_at_the_note_only(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, "2026-10-01", note="", category=Category.objects.get(key="groceries"))

    assert listed(get(client, TRANSACTIONS, me[1], search="groceries")) == []
    assert listed(get(client, TRANSACTIONS, me[1], search="Wallet")) == []


@pytest.mark.parametrize(
    ("term", "code"),
    [
        ("a", "min_length"),
        ("x" * 101, "max_length"),
        ("", "blank"),
        ("bad\nterm", "invalid_characters"),
    ],
)
def test_a_bad_search_is_a_400_on_search(
    client: APIClient, me: tuple[User, str], term: str, code: str
) -> None:
    response = get(client, TRANSACTIONS, me[1], search=term)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, "search")


def test_a_search_of_two_and_of_a_hundred_characters_is_fine(
    client: APIClient, me: tuple[User, str]
) -> None:
    assert get(client, TRANSACTIONS, me[1], search="ab").status_code == 200
    assert get(client, TRANSACTIONS, me[1], search="x" * 100).status_code == 200


def test_the_trigram_index_of_the_note_exists() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'ledger_tx_note_trgm'"
        )
        row = cursor.fetchone()

    assert row is not None
    assert "gin_trgm_ops" in row[0]
    assert "upper" in row[0].lower()


# --- bad filters ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "code", "attr"),
    [
        ({"date_from": "2026-13-01"}, "invalid", "date_from"),
        ({"date_to": "yesterday"}, "invalid", "date_to"),
        (
            {"date_from": "2026-10-31", "date_to": "2026-10-01"},
            "invalid_date_range",
            "date_to",
        ),
        ({"kind": "transfer"}, "invalid_choice", "kind"),
        ({"category_id": "not-an-id"}, "invalid", "category_id"),
        ({"account_id": "not-an-id"}, "invalid", "account_id"),
    ],
)
def test_a_bad_filter_is_a_400_on_its_parameter(
    client: APIClient,
    me: tuple[User, str],
    params: dict[str, str],
    code: str,
    attr: str,
) -> None:
    response = get(client, TRANSACTIONS, me[1], **params)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, attr)


def test_twenty_ids_are_fine_and_twenty_one_are_too_many(
    client: APIClient, me: tuple[User, str]
) -> None:
    ids = [f"0198f2a4-7c3e-7b1d-9a52-3e6f1d0c4b{n:02d}" for n in range(21)]

    ok = client.get(
        TRANSACTIONS + "?" + "&".join(f"category_id={i}" for i in ids[:20]),
        headers=bearer(me[1]),
    )
    many = client.get(
        TRANSACTIONS + "?" + "&".join(f"account_id={i}" for i in ids),
        headers=bearer(me[1]),
    )

    assert ok.status_code == 200
    assert many.status_code == 400
    error = first_error(many)
    assert (error["code"], error["attr"]) == ("too_many_values", "account_id")


def test_a_parameter_the_list_does_not_know_is_ignored(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = make(wallet, "2026-10-01")

    response = get(client, TRANSACTIONS, me[1], colour="blue", ordering="amount")

    assert listed(response) == [str(record.id)]


def test_the_cursor_keeps_the_filters_of_the_request(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for index in range(4):
        make(wallet, "2026-10-01", note=f"shop {index}")
        make(wallet, "2026-10-01", note=f"lunch {index}")

    pages = walk(client, me[1], search="shop", limit=3)

    assert [len(p) for p in pages] == [3, 1]
    assert (
        Transaction.objects.filter(
            pk__in=[i for page in pages for i in page], note__startswith="lunch"
        ).count()
        == 0
    )


def test_dates_outside_the_stored_range_are_still_valid_filters(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = get(
        client, TRANSACTIONS, me[1], date_from="1990-01-01", date_to="2150-12-31"
    )

    assert response.status_code == 200
