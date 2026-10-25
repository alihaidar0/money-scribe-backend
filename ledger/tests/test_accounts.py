"""Accounts: add, list, read, change, delete (ADR 0016; ``docs/api-fields.md``)."""

import pytest
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from idempotency.models import StoredResponse
from ledger.models import MAX_ACCOUNTS, Account
from ledger.tests.helpers import (
    ACCOUNTS,
    account_url,
    add_account,
    delete,
    fresh_key,
    get,
    names,
    patch,
    post,
)
from users.models import User
from users.tests.helpers import bearer, first_error

pytestmark = pytest.mark.django_db


def make(user: User, name: str, kind: str = "card") -> Account:
    return Account.objects.create(owner=user, name=name, kind=kind)


# --- adding -----------------------------------------------------------------------------------


def test_an_account_is_added_with_a_version_and_an_etag(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_account(client, me[1], name="  Travel card  ", kind="card")

    body = response.json()
    assert response.status_code == 201
    assert response["ETag"] == '"1"'
    assert response["Cache-Control"] == "no-store"
    assert set(body) == {"id", "name", "kind", "version", "created_at", "updated_at"}
    assert (body["name"], body["kind"], body["version"]) == ("Travel card", "card", 1)
    assert Account.objects.get(pk=body["id"]).owner == me[0]


def test_the_name_is_stored_normalized_to_nfc(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_account(client, me[1], name="Café")

    assert response.json()["name"] == "Café"


def test_a_create_needs_an_idempotency_key(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = client.post(
        ACCOUNTS, {"name": "Wallet", "kind": "cash"}, headers=bearer(me[1])
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "idempotency_key_required"
    assert not Account.objects.exists()


def test_a_repeated_create_returns_the_first_answer_and_adds_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    key = fresh_key()
    body = {"name": "Wallet", "kind": "cash"}

    first = post(client, ACCOUNTS, me[1], body, key)
    again = post(client, ACCOUNTS, me[1], body, key)

    assert (first.status_code, again.status_code) == (201, 201)
    assert again["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert Account.objects.count() == 1
    assert StoredResponse.objects.count() == 1


def test_the_same_key_with_another_body_is_422(
    client: APIClient, me: tuple[User, str]
) -> None:
    key = fresh_key()
    post(client, ACCOUNTS, me[1], {"name": "Wallet", "kind": "cash"}, key)

    other = post(client, ACCOUNTS, me[1], {"name": "Savings", "kind": "other"}, key)

    assert other.status_code == 422
    assert first_error(other)["code"] == "idempotency_key_reused"
    assert Account.objects.count() == 1


# --- the rules of the fields ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code", "attr"),
    [
        ({"kind": "card"}, "required", "name"),
        ({"name": "Wallet"}, "required", "kind"),
        ({"name": "", "kind": "card"}, "blank", "name"),
        ({"name": "   ", "kind": "card"}, "blank", "name"),
        ({"name": "x" * 31, "kind": "card"}, "max_length", "name"),
        ({"name": 12, "kind": "card"}, "invalid", "name"),
        ({"name": "Line\nbreak", "kind": "card"}, "invalid_characters", "name"),
        ({"name": "Evil\u202eone", "kind": "card"}, "invalid_characters", "name"),
        ({"name": "Wallet", "kind": "bank"}, "invalid_choice", "kind"),
        ({"name": "Wallet", "kind": "Card"}, "invalid_choice", "kind"),
    ],
)
def test_a_bad_field_is_a_400_on_that_field(
    client: APIClient,
    me: tuple[User, str],
    body: dict[str, object],
    code: str,
    attr: str,
) -> None:
    response = post(client, ACCOUNTS, me[1], body)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, attr)
    assert not Account.objects.exists()


def test_a_name_of_30_characters_is_the_longest(
    client: APIClient, me: tuple[User, str]
) -> None:
    assert add_account(client, me[1], name="x" * 30).status_code == 201


@pytest.mark.parametrize(
    "field", ["id", "version", "created_at", "updated_at", "owner", "foo"]
)
def test_a_field_that_cannot_be_written_is_400_read_only(
    client: APIClient, me: tuple[User, str], field: str
) -> None:
    response = post(
        client, ACCOUNTS, me[1], {"name": "Wallet", "kind": "cash", field: "x"}
    )

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("read_only", field)
    assert not Account.objects.exists()


# --- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize("second", ["Wallet", "wallet", "WALLET", "  Wallet  "])
def test_a_name_is_unique_per_person_in_any_case(
    client: APIClient, me: tuple[User, str], second: str
) -> None:
    add_account(client, me[1], name="Wallet", kind="cash")

    response = add_account(client, me[1], name=second, kind="cash")

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("name_taken", "name")
    assert Account.objects.count() == 1


def test_a_name_that_differs_only_by_normalization_is_taken(
    client: APIClient, me: tuple[User, str]
) -> None:
    add_account(client, me[1], name="Café")

    response = add_account(client, me[1], name="Café")

    assert first_error(response)["code"] == "name_taken"


def test_two_people_can_use_the_same_name(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    assert add_account(client, me[1], name="Wallet").status_code == 201
    assert add_account(client, other[1], name="Wallet").status_code == 201


def test_the_database_refuses_a_name_taken_in_the_same_moment(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch, client: APIClient
) -> None:
    """Two requests that pass the check together meet the unique index: the second is the same 400."""
    make(me[0], "Wallet")
    monkeypatch.setattr(
        "ledger.services.ensure_account_name_free", lambda *a, **k: None
    )

    response = add_account(client, me[1], name="wallet")

    assert response.status_code == 400
    assert first_error(response)["code"] == "name_taken"
    assert Account.objects.count() == 1


# --- the limit --------------------------------------------------------------------------------


def test_a_person_has_at_most_20_accounts(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    for index in range(MAX_ACCOUNTS):
        make(me[0], f"Account {index}")

    response = add_account(client, me[1], name="One too many")

    assert MAX_ACCOUNTS == 20
    assert response.status_code == 409
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("account_limit_reached", None)
    assert Account.objects.filter(owner=me[0]).count() == 20
    assert add_account(client, other[1]).status_code == 201  # the limit is per person


# --- the list ---------------------------------------------------------------------------------


def test_the_list_is_in_the_order_the_accounts_were_made(
    client: APIClient, me: tuple[User, str]
) -> None:
    for name in ["Third?", "Second?", "First?"]:
        make(me[0], name)

    response = get(client, ACCOUNTS, me[1])

    assert response.status_code == 200
    assert names(response) == ["Third?", "Second?", "First?"]
    assert response.json()["next"] is None
    assert set(response.json()["results"][0]) == {
        "id",
        "name",
        "kind",
        "version",
        "created_at",
        "updated_at",
    }


def test_the_list_pages_by_cursor_without_a_gap_or_a_repeat(
    client: APIClient, me: tuple[User, str]
) -> None:
    made = [make(me[0], f"Account {n}") for n in range(5)]

    seen: list[str] = []
    page = get(client, ACCOUNTS, me[1], limit=2)
    while True:
        assert page.status_code == 200
        seen += [item["id"] for item in page.json()["results"]]
        link = page.json()["next"]
        if link is None:
            break
        page = client.get(link, headers=bearer(me[1]))

    assert seen == [str(a.id) for a in made]


def test_the_list_holds_only_the_persons_own_accounts(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    make(me[0], "Mine")
    make(other[0], "Theirs")

    assert names(get(client, ACCOUNTS, me[1])) == ["Mine"]


def test_a_person_with_no_account_gets_an_empty_list(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = get(client, ACCOUNTS, me[1])

    assert response.status_code == 200
    assert response.json() == {"next": None, "previous": None, "results": []}


def test_a_hundred_per_page_is_the_most(
    client: APIClient, me: tuple[User, str]
) -> None:
    make(me[0], "Only one")

    assert get(client, ACCOUNTS, me[1], limit=100).status_code == 200
    page = get(client, ACCOUNTS, me[1], limit=0)
    assert first_error(page)["attr"] == "limit"


# --- reading and changing ---------------------------------------------------------------------


def test_an_account_is_read_with_its_etag(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "Wallet", "cash")

    response = get(client, account_url(account.id), me[1])

    assert response.status_code == 200
    assert response["ETag"] == '"1"'
    assert response.json()["name"] == "Wallet"


def test_a_change_raises_the_version_and_answers_the_new_etag(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "Wallet", "cash")

    response = patch(
        client,
        account_url(account.id),
        me[1],
        {"name": "Main wallet", "kind": "other"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response["ETag"] == '"2"'
    body = response.json()
    assert (body["name"], body["kind"], body["version"]) == ("Main wallet", "other", 2)
    account.refresh_from_db()
    assert (account.name, account.version) == ("Main wallet", 2)


def test_a_change_can_be_just_one_field(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "Wallet", "cash")

    response = patch(client, account_url(account.id), me[1], {"kind": "other"})

    assert response.status_code == 200
    assert (response.json()["name"], response.json()["kind"]) == ("Wallet", "other")


def test_a_change_that_changes_nothing_keeps_the_version(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "Wallet", "cash")

    same = patch(
        client, account_url(account.id), me[1], {"name": "Wallet", "kind": "cash"}
    )
    empty = patch(client, account_url(account.id), me[1], {})

    assert (same.status_code, empty.status_code) == (200, 200)
    assert same.json()["version"] == empty.json()["version"] == 1


def test_an_account_can_change_the_case_of_its_own_name(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "wallet", "cash")

    response = patch(client, account_url(account.id), me[1], {"name": "Wallet"})

    assert response.status_code == 200
    assert response.json()["name"] == "Wallet"


def test_a_change_to_a_name_that_is_taken_is_refused(
    client: APIClient, me: tuple[User, str]
) -> None:
    make(me[0], "Wallet")
    account = make(me[0], "Savings")

    response = patch(client, account_url(account.id), me[1], {"name": "wallet"})

    assert response.status_code == 400
    assert first_error(response)["code"] == "name_taken"
    account.refresh_from_db()
    assert account.name == "Savings"


@pytest.mark.parametrize("field", ["id", "version", "created_at", "updated_at", "foo"])
def test_a_change_of_a_field_that_cannot_be_written_is_400_read_only(
    client: APIClient, me: tuple[User, str], field: str
) -> None:
    account = make(me[0], "Wallet")

    response = patch(client, account_url(account.id), me[1], {field: "x"})

    assert response.status_code == 400
    assert first_error(response)["code"] == "read_only"
    account.refresh_from_db()
    assert account.version == 1


def test_a_stale_change_is_412_and_changes_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "Wallet")
    patch(client, account_url(account.id), me[1], {"name": "Second"})

    stale = patch(
        client, account_url(account.id), me[1], {"name": "Third"}, **{"If-Match": '"1"'}
    )

    assert stale.status_code == 412
    assert first_error(stale)["code"] == "precondition_failed"
    account.refresh_from_db()
    assert (account.name, account.version) == ("Second", 2)


def test_a_change_without_if_match_is_the_last_write(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "Wallet")

    assert (
        patch(client, account_url(account.id), me[1], {"name": "A"}).status_code == 200
    )
    assert (
        patch(client, account_url(account.id), me[1], {"name": "B"}).status_code == 200
    )


def test_put_is_not_offered(client: APIClient, me: tuple[User, str]) -> None:
    account = make(me[0], "Wallet")

    response = client.put(
        account_url(account.id),
        {"name": "x", "kind": "card"},
        headers=bearer(me[1]),
    )

    assert response.status_code == 405


# --- deleting ---------------------------------------------------------------------------------


def test_an_account_is_deleted_and_a_repeat_is_404(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "Wallet")

    first = delete(client, account_url(account.id), me[1], **{"If-Match": '"1"'})
    again = delete(client, account_url(account.id), me[1])

    assert first.status_code == 204
    assert first.content == b""
    assert again.status_code == 404
    assert first_error(again)["code"] == "not_found"
    assert get(client, account_url(account.id), me[1]).status_code == 404
    assert not Account.objects.exists()


def test_a_stale_delete_is_412_and_deletes_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    account = make(me[0], "Wallet")
    patch(client, account_url(account.id), me[1], {"name": "Renamed"})

    stale = delete(client, account_url(account.id), me[1], **{"If-Match": '"1"'})

    assert stale.status_code == 412
    assert Account.objects.filter(pk=account.pk).exists()


# --- ownership and access ---------------------------------------------------------------------


def test_another_persons_account_is_404_for_every_method(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    theirs = make(other[0], "Theirs")
    mine = APIClient()
    mine.raise_request_exception = False
    mine.credentials(HTTP_AUTHORIZATION=f"Bearer {me[1]}")

    assert_hidden_from_other_users(
        mine, account_url(theirs.id), methods=("get", "patch", "delete")
    )

    theirs.refresh_from_db()
    assert (theirs.name, theirs.version) == ("Theirs", 1)


def test_every_operation_needs_a_signed_in_person(client: APIClient) -> None:
    some = "0198f2a4-7c3e-7b1d-9a52-3e6f1d0c4b21"

    assert client.get(ACCOUNTS).status_code == 401
    assert client.post(ACCOUNTS, {}).status_code == 401
    assert client.get(account_url(some)).status_code == 401
    assert client.patch(account_url(some), {}).status_code == 401
    assert client.delete(account_url(some)).status_code == 401


def test_an_id_that_is_not_a_uuid_is_not_a_route(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = get(client, f"{ACCOUNTS}not-an-id/", me[1])

    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")
