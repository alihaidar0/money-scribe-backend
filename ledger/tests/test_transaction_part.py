"""The optional part of an income transaction (ADR 0023)."""

import pytest
from rest_framework.test import APIClient

from ledger.models import Account
from ledger.tests.helpers import (
    add_transaction,
    built_in_id,
    patch,
    transaction_url,
)
from users.models import User
from users.tests.helpers import bearer, first_error

pytestmark = pytest.mark.django_db

INCOME = {"kind": "income", "category_id": built_in_id("scholarship")}


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


def test_an_income_without_a_part_shows_the_part_of_the_nearest_pay_day(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    body = add_transaction(client, me[1], wallet.id, **INCOME, date="2026-10-03").json()

    assert (body["part"], body["part_month"]) == ("second", "2026-09")


def test_the_part_follows_the_pay_days_in_the_settings(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    client.patch(
        "/api/v1/settings/",
        {"second_pay_day": 2, "first_pay_day": 10},
        headers=bearer(me[1]),
    )
    created = add_transaction(client, me[1], wallet.id, **INCOME, date="2026-10-08")

    # With 20 and 5 the 8th would join September's second part; with 10 and 2 the 10th is nearest.
    assert (created.json()["part"], created.json()["part_month"]) == (
        "first",
        "2026-10",
    )


def test_a_chosen_part_is_stored_and_shown(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    response = add_transaction(
        client, me[1], wallet.id, **INCOME, part="first", part_month="2026-11"
    )

    assert response.status_code == 201, response.json()
    assert (response.json()["part"], response.json()["part_month"]) == (
        "first",
        "2026-11",
    )
    read = client.get(transaction_url(response.json()["id"]), headers=bearer(me[1]))
    assert (read.json()["part"], read.json()["part_month"]) == ("first", "2026-11")


def test_an_expense_has_no_part(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    body = add_transaction(client, me[1], wallet.id).json()

    assert (body["part"], body["part_month"]) == (None, None)


@pytest.mark.parametrize(
    "fields",
    [
        {"part": "first"},
        {"part_month": "2026-10"},
        {"part": "first", "part_month": None},
        {"part": None, "part_month": "2026-10"},
    ],
)
def test_the_part_and_its_month_come_together(
    client: APIClient,
    me: tuple[User, str],
    wallet: Account,
    fields: dict[str, str | None],
) -> None:
    response = add_transaction(client, me[1], wallet.id, **INCOME, **fields)

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_part"


def test_an_expense_cannot_be_given_a_part(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    response = add_transaction(
        client, me[1], wallet.id, part="first", part_month="2026-10"
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_part"


@pytest.mark.parametrize(
    "month", ["2026-13", "2026-10-01", "1999-12", "2100-01", 202610]
)
def test_a_part_month_is_yyyy_mm_in_range(
    client: APIClient, me: tuple[User, str], wallet: Account, month: object
) -> None:
    response = add_transaction(
        client, me[1], wallet.id, **INCOME, part="first", part_month=month
    )

    assert response.status_code == 400
    assert first_error(response)["attr"] == "part_month"


def test_a_part_can_be_set_changed_and_cleared(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    created = add_transaction(client, me[1], wallet.id, **INCOME, date="2026-10-24")
    url = transaction_url(created.json()["id"])

    set_it = patch(
        client,
        url,
        me[1],
        {"part": "second", "part_month": "2026-10"},
        **{"If-Match": '"1"'},
    )
    cleared = patch(
        client, url, me[1], {"part": None, "part_month": None}, **{"If-Match": '"2"'}
    )

    assert (set_it.json()["part"], set_it.json()["part_month"]) == ("second", "2026-10")
    assert (cleared.json()["part"], cleared.json()["part_month"]) == (
        "first",
        "2026-10",
    )


def test_one_of_the_two_sent_alone_in_a_change_is_refused(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    created = add_transaction(client, me[1], wallet.id, **INCOME)

    response = patch(
        client,
        transaction_url(created.json()["id"]),
        me[1],
        {"part": "first"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_part"


def test_turning_an_income_into_an_expense_drops_its_part(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    created = add_transaction(
        client, me[1], wallet.id, **INCOME, part="first", part_month="2026-10"
    )

    changed = patch(
        client,
        transaction_url(created.json()["id"]),
        me[1],
        {"kind": "expense", "category_id": built_in_id("other")},
        **{"If-Match": '"1"'},
    )

    assert changed.status_code == 200, changed.json()
    assert (changed.json()["part"], changed.json()["part_month"]) == (None, None)


def test_an_expense_cannot_get_a_part_in_a_change(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    created = add_transaction(client, me[1], wallet.id)

    response = patch(
        client,
        transaction_url(created.json()["id"]),
        me[1],
        {"part": "first", "part_month": "2026-10"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_part"
