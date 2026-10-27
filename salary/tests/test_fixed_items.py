"""The fixed items of a month: add, change, delete (ADR 0007, ADR 0016)."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from idempotency.models import StoredResponse
from salary.models import MAX_FIXED_ITEMS, FixedItem, SalaryMonth
from salary.tests.helpers import (
    API,
    add_item,
    fresh_key,
    item_url,
    month_body,
    put_month,
    read_month,
)
from users.models import User
from users.tests.helpers import bearer, first_error, start_session

pytestmark = pytest.mark.django_db


@pytest.fixture
def month(client: APIClient, account: tuple[User, str]) -> str:
    put_month(client, account[1])
    return "2026-10"


def patch(
    client: APIClient, token: str, item_id: str, body: dict[str, Any], **headers: str
) -> Any:
    return client.patch(item_url(item_id), body, headers={**bearer(token), **headers})


# --- adding -----------------------------------------------------------------------------------


def test_an_item_is_added_with_its_own_version_and_etag(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    response = add_item(
        client,
        account[1],
        body={
            "name": "  Rent  ",
            "amount": "3000.00",
            "kind": "expense",
            "part": "second",
        },
    )

    body = response.json()
    assert response.status_code == 201
    assert response["ETag"] == '"1"'
    assert body["name"] == "Rent"  # trimmed
    assert body["amount"] == "3000.00"
    assert body["currency"] == "RUB"
    assert (body["kind"], body["part"]) == ("expense", "second")
    assert body["version"] == 1
    assert body["id"]
    assert body["created_at"].endswith("Z")


def test_the_part_defaults_to_the_first(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    response = add_item(client, account[1])

    assert response.json()["part"] == "first"


def test_the_day_is_stored_as_sent_and_never_changes_the_part(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    response = add_item(
        client,
        account[1],
        body={
            "name": "Rent",
            "amount": "3000.00",
            "kind": "expense",
            "part": "first",
            "day": 31,
        },
    )

    body = response.json()
    assert response.status_code == 201
    # 31 is kept as sent, even though it is past the end of some months
    assert (body["day"], body["part"]) == (31, "first")
    assert FixedItem.objects.get().day == 31
    assert read_month(client, account[1]).json()["fixed_items"][0]["day"] == 31


@pytest.mark.parametrize("sent", [{}, {"day": None}])
def test_an_item_without_a_day_has_a_null_day(
    client: APIClient, account: tuple[User, str], month: str, sent: dict[str, Any]
) -> None:
    body = {"name": "Rent", "amount": "3000.00", "kind": "expense", **sent}

    response = add_item(client, account[1], body=body)

    assert response.status_code == 201
    assert response.json()["day"] is None
    assert FixedItem.objects.get().day is None


@pytest.mark.parametrize("day", [1, 15, 16, 31])
def test_the_edges_of_the_day_are_accepted(
    client: APIClient, account: tuple[User, str], month: str, day: int
) -> None:
    body = {"name": "Rent", "amount": "30.00", "kind": "expense", "day": day}

    assert add_item(client, account[1], body=body).json()["day"] == day


def test_the_currency_may_be_left_out_or_be_the_months(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    sent = {"name": "Rent", "amount": "3000.00", "kind": "expense", "currency": "RUB"}

    assert add_item(client, account[1], body=sent).status_code == 201


def test_another_currency_than_the_months_is_a_currency_mismatch_on_currency(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    response = add_item(
        client,
        account[1],
        body={"name": "Rent", "amount": "30.00", "kind": "expense", "currency": "USD"},
    )

    assert response.status_code == 400
    assert (first_error(response)["code"], first_error(response)["attr"]) == (
        "currency_mismatch",
        "currency",
    )
    assert FixedItem.objects.count() == 0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("name", "", "blank"),
        ("name", "   ", "blank"),
        ("name", "x" * 51, "max_length"),
        ("name", None, "null"),
        ("amount", "0.00", "invalid_amount"),
        ("amount", "-5.00", "invalid_amount"),
        ("amount", "5", "invalid_amount"),
        ("amount", "5.000", "invalid_amount"),
        ("amount", 5, "invalid_amount"),
        ("amount", 5.5, "invalid_amount"),
        ("kind", "gift", "invalid_choice"),
        ("part", "third", "invalid_choice"),
        ("day", 0, "min_value"),
        ("day", 32, "max_value"),
        ("day", "x", "invalid"),
        ("day", 1.5, "invalid"),
        ("currency", "XXX", "invalid_choice"),
    ],
)
def test_each_wrong_field_is_a_400_with_its_own_code(
    client: APIClient,
    account: tuple[User, str],
    month: str,
    field: str,
    value: Any,
    code: str,
) -> None:
    body = {"name": "Rent", "amount": "30.00", "kind": "expense", field: value}

    response = add_item(client, account[1], body=body)

    assert response.status_code == 400
    assert (first_error(response)["attr"], first_error(response)["code"]) == (
        field,
        code,
    )
    assert FixedItem.objects.count() == 0


def test_a_name_of_exactly_fifty_characters_is_accepted(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    body = {"name": "x" * 50, "amount": "1.00", "kind": "income"}

    assert add_item(client, account[1], body=body).status_code == 201


def test_the_amount_follows_the_digits_of_the_months_currency(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(
        client,
        account[1],
        "2026-09",
        month_body(currency="JPY", base_salary="250000", food_per_day="1000"),
    )

    ok = add_item(
        client,
        account[1],
        "2026-09",
        {"name": "Rent", "amount": "30000", "kind": "expense"},
    )
    bad = add_item(
        client,
        account[1],
        "2026-09",
        {"name": "Rent", "amount": "30000.00", "kind": "expense"},
    )

    assert ok.status_code == 201
    assert first_error(bad)["code"] == "invalid_amount"


def test_an_item_needs_a_saved_month(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = add_item(client, account[1], "2026-10")

    assert response.status_code == 404
    assert FixedItem.objects.count() == 0


def test_another_users_month_is_not_there_to_add_to(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    stranger = APIClient()
    _, body = start_session(stranger)

    response = add_item(stranger, body["access_token"], month)

    assert response.status_code == 404
    assert FixedItem.objects.count() == 0


# --- the key ----------------------------------------------------------------------------------


def test_adding_needs_the_idempotency_key_and_a_repeat_adds_nothing(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    missing = client.post(
        f"{API}/months/{month}/fixed-items/",
        {"name": "Rent", "amount": "30.00", "kind": "expense"},
        headers=bearer(account[1]),
    )
    key = fresh_key()
    first = add_item(client, account[1], key=key)
    again = add_item(client, account[1], key=key)

    assert first_error(missing)["code"] == "idempotency_key_required"
    assert again.status_code == 201
    assert again.json() == first.json()
    assert again["Idempotent-Replayed"] == "true"
    assert FixedItem.objects.count() == 1


def test_the_same_key_for_another_item_is_a_422(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    key = fresh_key()
    add_item(client, account[1], key=key)

    other = add_item(
        client,
        account[1],
        body={"name": "Food", "amount": "1.00", "kind": "expense"},
        key=key,
    )

    assert other.status_code == 422
    assert first_error(other)["code"] == "idempotency_key_reused"
    assert FixedItem.objects.count() == 1


def test_a_refused_add_leaves_no_key(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    key = fresh_key()
    refused = add_item(
        client,
        account[1],
        body={"name": "", "amount": "1.00", "kind": "income"},
        key=key,
    )

    assert refused.status_code == 400
    assert StoredResponse.objects.count() == 0
    assert add_item(client, account[1], key=key).status_code == 201


# --- the limit --------------------------------------------------------------------------------


def fill(owner: User, count: int) -> None:
    month = SalaryMonth.objects.get(owner=owner)
    FixedItem.objects.bulk_create(
        FixedItem(salary_month=month, name=f"item {n}", amount=100, kind="expense")
        for n in range(count)
    )


def test_a_month_holds_a_hundred_items_and_the_hundred_and_first_is_a_409(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    fill(account[0], MAX_FIXED_ITEMS - 1)

    last = add_item(client, account[1])
    over = add_item(client, account[1])

    assert last.status_code == 201
    assert over.status_code == 409
    assert first_error(over)["code"] == "fixed_item_limit_reached"
    assert first_error(over)["attr"] is None
    assert FixedItem.objects.count() == MAX_FIXED_ITEMS


def test_deleting_one_makes_room_again(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    fill(account[0], MAX_FIXED_ITEMS)
    victim = FixedItem.objects.first()
    assert victim is not None
    client.delete(item_url(str(victim.id)), headers=bearer(account[1]))

    assert add_item(client, account[1]).status_code == 201


def test_the_limit_counts_each_month_on_its_own(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    fill(account[0], MAX_FIXED_ITEMS)
    put_month(client, account[1], "2026-09")

    assert add_item(client, account[1], "2026-09").status_code == 201


# --- the totals and the balances move with the items ------------------------------------------


def test_an_item_moves_the_totals_and_the_balances_but_not_the_month_version(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    before = read_month(client, account[1]).json()
    add_item(
        client,
        account[1],
        body={"name": "Bonus", "amount": "2500.00", "kind": "income", "part": "second"},
    )

    after = read_month(client, account[1]).json()

    assert after["version"] == before["version"] == 1
    assert after["fixed_totals"]["second"] == {"income": "2500.00", "expense": "0.00"}
    assert after["balances"]["second"] == "63400.00"
    assert after["balances"]["month"] == "106900.00"
    assert after["results"] == before["results"]


def test_an_amount_that_would_make_the_month_unreadable_is_refused_and_not_stored(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    response = add_item(
        client,
        account[1],
        body={"name": "Huge", "amount": "9999999999999.99", "kind": "income"},
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_amount"
    assert FixedItem.objects.count() == 0
    assert read_month(client, account[1]).status_code == 200


# --- changing ---------------------------------------------------------------------------------


@pytest.fixture
def item(client: APIClient, account: tuple[User, str], month: str) -> dict[str, Any]:
    created: dict[str, Any] = add_item(client, account[1]).json()
    return created


def test_a_change_applies_only_the_fields_sent_and_raises_the_version(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    response = patch(client, account[1], item["id"], {"amount": "3200.00"})

    body = response.json()
    assert response.status_code == 200
    assert response["ETag"] == '"2"'
    assert (body["name"], body["amount"], body["kind"], body["part"]) == (
        "Rent",
        "3200.00",
        "expense",
        "first",
    )
    assert body["version"] == 2
    assert body["currency"] == "RUB"


def test_every_field_can_change(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    response = patch(
        client,
        account[1],
        item["id"],
        {"name": " Bonus ", "amount": "10.00", "kind": "income", "part": "second"},
    )

    body = response.json()
    assert (body["name"], body["amount"], body["kind"], body["part"]) == (
        "Bonus",
        "10.00",
        "income",
        "second",
    )


def test_the_day_can_be_set_changed_cleared_and_is_kept_by_other_changes(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    token = account[1]
    assert item["day"] is None

    set_day = patch(client, token, item["id"], {"day": 16})
    assert (set_day.status_code, set_day.json()["day"], set_day.json()["part"]) == (
        200,
        16,
        "first",
    )
    assert set_day.json()["version"] == 2

    other_field = patch(client, token, item["id"], {"amount": "10.00"})
    assert other_field.json()["day"] == 16  # a change that leaves `day` out keeps it

    cleared = patch(client, token, item["id"], {"day": None})
    assert cleared.json()["day"] is None
    assert FixedItem.objects.get().day is None


def test_a_wrong_day_on_a_change_is_a_400_and_keeps_the_item(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    patch(client, account[1], item["id"], {"day": 5})

    response = patch(client, account[1], item["id"], {"day": 32})

    assert response.status_code == 400
    assert (first_error(response)["attr"], first_error(response)["code"]) == (
        "day",
        "max_value",
    )
    assert FixedItem.objects.get().day == 5


def test_a_change_moves_the_month_balances(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    patch(client, account[1], item["id"], {"amount": "4000.00", "part": "second"})

    month = read_month(client, account[1]).json()

    assert month["balances"] == {
        "first": "43500.00",
        "second": "56900.00",
        "month": "100400.00",
    }
    assert month["version"] == 1


def test_a_change_from_a_stale_version_is_a_412_and_changes_nothing(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    patch(client, account[1], item["id"], {"amount": "1.00"})

    stale = patch(
        client, account[1], item["id"], {"amount": "2.00"}, **{"If-Match": '"1"'}
    )
    fresh = patch(
        client, account[1], item["id"], {"amount": "3.00"}, **{"If-Match": '"2"'}
    )

    assert (stale.status_code, fresh.status_code) == (412, 200)
    assert first_error(stale)["code"] == "precondition_failed"
    assert FixedItem.objects.get().amount == 300


def test_without_if_match_the_last_change_wins(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    patch(client, account[1], item["id"], {"amount": "1.00"})

    last = patch(client, account[1], item["id"], {"amount": "2.00"})

    assert last.json()["version"] == 3


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"currency": "USD"}, "currency_mismatch"),
        ({"amount": "0.00"}, "invalid_amount"),
        ({"amount": "5"}, "invalid_amount"),
        ({"name": ""}, "blank"),
        ({"kind": "gift"}, "invalid_choice"),
    ],
)
def test_a_wrong_change_is_a_400_and_keeps_the_item(
    client: APIClient,
    account: tuple[User, str],
    item: dict[str, Any],
    body: dict[str, Any],
    code: str,
) -> None:
    response = patch(client, account[1], item["id"], body)

    assert response.status_code == 400
    assert first_error(response)["code"] == code
    assert FixedItem.objects.get().version == 1


def test_an_amount_that_would_make_the_month_unreadable_is_refused_on_a_change_too(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    response = patch(
        client, account[1], item["id"], {"kind": "income", "amount": "9999999999999.99"}
    )

    assert response.status_code == 400
    assert FixedItem.objects.get().amount == 300000


def test_fields_that_are_not_the_clients_to_set_are_ignored(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    response = patch(
        client,
        account[1],
        item["id"],
        {
            "version": 50,
            "id": "x",
            "salary_month": "y",
            "created_at": "2000-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["id"] == item["id"]


# --- deleting ---------------------------------------------------------------------------------


def test_an_item_is_deleted_and_a_repeat_is_a_404(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    first = client.delete(item_url(item["id"]), headers=bearer(account[1]))
    again = client.delete(item_url(item["id"]), headers=bearer(account[1]))

    assert (first.status_code, again.status_code) == (204, 404)
    assert read_month(client, account[1]).json()["fixed_items"] == []


def test_a_delete_from_a_stale_version_is_a_412_and_keeps_the_item(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    patch(client, account[1], item["id"], {"amount": "1.00"})

    refused = client.delete(
        item_url(item["id"]), headers={**bearer(account[1]), "If-Match": '"1"'}
    )

    assert refused.status_code == 412
    assert FixedItem.objects.count() == 1


def test_only_changing_and_deleting_exist_for_one_item(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    assert (
        client.get(item_url(item["id"]), headers=bearer(account[1])).status_code == 405
    )
    assert (
        client.put(item_url(item["id"]), {}, headers=bearer(account[1])).status_code
        == 405
    )


def test_another_users_item_is_hidden(
    client: APIClient, account: tuple[User, str], item: dict[str, Any]
) -> None:
    stranger = APIClient()
    _, body = start_session(stranger)
    stranger.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")

    assert_hidden_from_other_users(
        stranger, item_url(item["id"]), methods=("patch", "delete")
    )
    assert FixedItem.objects.count() == 1


def test_the_items_of_a_month_are_shown_in_the_order_they_were_added(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    for name in ("b", "a", "c"):
        add_item(
            client, account[1], body={"name": name, "amount": "1.00", "kind": "income"}
        )

    names = [i["name"] for i in read_month(client, account[1]).json()["fixed_items"]]

    assert names == ["b", "a", "c"]


# --- the text rules (ADR 0021) ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["Rent\nsecond line", "Rent\u202eevil", "Rent\x00"])
def test_a_name_with_a_forbidden_character_is_refused_on_create_and_edit(
    client: APIClient, account: tuple[User, str], month: str, name: str
) -> None:
    refused = add_item(
        client,
        account[1],
        body={"name": name, "amount": "3000.00", "kind": "expense"},
    )
    created = add_item(client, account[1]).json()
    edited = patch(client, account[1], created["id"], {"name": name})

    for response in (refused, edited):
        assert response.status_code == 400
        error = first_error(response)
        assert (error["code"], error["attr"]) == ("invalid_characters", "name")
    assert FixedItem.objects.get(pk=created["id"]).name == "Rent"


def test_an_arabic_name_with_marks_is_accepted_and_normalized(
    client: APIClient, account: tuple[User, str], month: str
) -> None:
    response = add_item(
        client,
        account[1],
        body={"name": "  إيجار\u200f شهري  ", "amount": "3000.00", "kind": "expense"},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "إيجار\u200f شهري"
