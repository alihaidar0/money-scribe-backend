"""Transactions: add, read, change, delete (ADR 0008, 0016; ``docs/api-fields.md``)."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from idempotency.models import StoredResponse
from ledger.models import (
    MAX_TRANSACTIONS,
    Account,
    Category,
    Transaction,
)
from ledger.tests.helpers import (
    TRANSACTIONS,
    add_transaction,
    built_in_id,
    delete,
    fresh_key,
    get,
    patch,
    post,
    transaction_body,
    transaction_url,
)
from users.models import User
from users.tests.helpers import bearer, first_error

pytestmark = pytest.mark.django_db


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


def stored(account: Account, **fields: Any) -> Transaction:
    """A transaction made straight in the database."""
    values: dict[str, Any] = {
        "owner": account.owner,
        "kind": "expense",
        "amount": 1250,
        "currency": "USD",
        "date": "2026-10-24",
        "category": Category.objects.get(key="groceries"),
        "account": account,
        "note": "",
    }
    return Transaction.objects.create(**{**values, **fields})


# --- adding -----------------------------------------------------------------------------------


def test_a_transaction_is_added_with_its_version_and_etag(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    response = add_transaction(client, me[1], wallet.id)

    body = response.json()
    assert response.status_code == 201
    assert response["ETag"] == '"1"'
    assert response["Cache-Control"] == "no-store"
    assert set(body) == {
        "id",
        "kind",
        "amount",
        "currency",
        "date",
        "category_id",
        "account_id",
        "note",
        "version",
        "created_at",
        "updated_at",
    }
    assert (body["kind"], body["amount"], body["currency"], body["date"]) == (
        "expense",
        "12.50",
        "USD",
        "2026-10-24",
    )
    assert (body["category_id"], body["account_id"], body["note"]) == (
        built_in_id("groceries"),
        str(wallet.id),
        "Weekly shop",
    )
    row = Transaction.objects.get(pk=body["id"])
    assert (row.owner, row.amount, row.currency, row.version) == (me[0], 1250, "USD", 1)


def test_the_note_is_optional_and_trimmed_and_normalized(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    plain = add_transaction(client, me[1], wallet.id)
    body = transaction_body(wallet.id)
    del body["note"]
    without = post(client, TRANSACTIONS, me[1], body)
    spaced = add_transaction(client, me[1], wallet.id, note="  Café  ")

    assert plain.json()["note"] == "Weekly shop"
    assert without.json()["note"] == ""
    assert spaced.json()["note"] == "Café"
    assert add_transaction(client, me[1], wallet.id, note="").json()["note"] == ""


def test_a_create_needs_an_idempotency_key(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    response = client.post(
        TRANSACTIONS, transaction_body(wallet.id), headers=bearer(me[1])
    )

    assert first_error(response)["code"] == "idempotency_key_required"
    assert not Transaction.objects.exists()


def test_a_repeated_create_returns_the_first_answer_and_adds_nothing(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    key = fresh_key()
    body = transaction_body(wallet.id)

    first = post(client, TRANSACTIONS, me[1], body, key)
    again = post(client, TRANSACTIONS, me[1], body, key)
    other = post(client, TRANSACTIONS, me[1], {**body, "amount": "99.00"}, key)

    assert (first.status_code, again.status_code) == (201, 201)
    assert again["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert other.status_code == 422
    assert Transaction.objects.count() == 1
    assert StoredResponse.objects.count() == 1


# --- the amount and the currency --------------------------------------------------------------


@pytest.mark.parametrize(
    "amount",
    [
        "0.00",
        "-1.00",
        "12.5",
        "12.500",
        "1e3",
        "12,50",
        "",
        " 12.50",
        "+12.50",
        "012.50",
        "12.",
        ".50",
    ],
)
def test_a_bad_amount_is_invalid_amount_on_the_field(
    client: APIClient, me: tuple[User, str], wallet: Account, amount: str
) -> None:
    response = add_transaction(client, me[1], wallet.id, amount=amount)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("invalid_amount", "amount")
    assert not Transaction.objects.exists()


@pytest.mark.parametrize("amount", [12.5, 12, True, None, ["12.50"]])
def test_an_amount_that_is_not_a_string_is_refused(
    client: APIClient, me: tuple[User, str], wallet: Account, amount: Any
) -> None:
    response = add_transaction(client, me[1], wallet.id, amount=amount)

    assert response.status_code == 400
    assert first_error(response)["attr"] == "amount"


@pytest.mark.parametrize(
    ("currency", "amount", "stored_minor"),
    [("USD", "12.50", 1250), ("KWD", "12.500", 12500), ("JPY", "1200", 1200)],
)
def test_the_amount_has_exactly_the_digits_of_its_currency(
    client: APIClient,
    me: tuple[User, str],
    wallet: Account,
    currency: str,
    amount: str,
    stored_minor: int,
) -> None:
    response = add_transaction(
        client, me[1], wallet.id, currency=currency, amount=amount
    )

    assert response.status_code == 201
    assert response.json()["amount"] == amount
    assert Transaction.objects.get().amount == stored_minor


def test_too_many_or_too_few_digits_for_the_currency_are_refused(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for currency, amount in (("JPY", "1200.00"), ("KWD", "12.50"), ("USD", "12")):
        response = add_transaction(
            client, me[1], wallet.id, currency=currency, amount=amount
        )

        assert first_error(response)["code"] == "invalid_amount", (currency, amount)


def test_the_largest_amount_is_999_999_999_999_999_minor_units(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    ok = add_transaction(client, me[1], wallet.id, amount="9999999999999.99")
    too_big = add_transaction(client, me[1], wallet.id, amount="10000000000000.00")

    assert ok.status_code == 201
    assert first_error(too_big)["code"] == "invalid_amount"


@pytest.mark.parametrize("currency", ["usd", "XXX", "US", "", None, 840])
def test_an_unsupported_currency_is_refused(
    client: APIClient, me: tuple[User, str], wallet: Account, currency: Any
) -> None:
    response = add_transaction(client, me[1], wallet.id, currency=currency)

    assert response.status_code == 400
    assert first_error(response)["attr"] == "currency"


def test_a_missing_amount_currency_kind_or_date_is_required(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for field in ("amount", "currency", "kind", "date", "category_id", "account_id"):
        body = transaction_body(wallet.id)
        del body[field]

        response = post(client, TRANSACTIONS, me[1], body)

        error = first_error(response)
        assert (error["code"], error["attr"]) == ("required", field), field


# --- the other fields -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["transfer", "Expense", "", None])
def test_an_unknown_kind_is_refused(
    client: APIClient, me: tuple[User, str], wallet: Account, kind: Any
) -> None:
    response = add_transaction(client, me[1], wallet.id, kind=kind)

    assert first_error(response)["attr"] == "kind"


@pytest.mark.parametrize(
    ("date", "code"),
    [
        ("2026-13-01", "invalid"),
        ("2026-02-30", "invalid"),
        ("24/10/2026", "invalid"),
        ("2026-10-24T10:00:00Z", "invalid"),
        (20261024, "invalid"),
        ("1999-12-31", "date_out_of_range"),
        ("2100-01-01", "date_out_of_range"),
        ("0001-01-01", "date_out_of_range"),
    ],
)
def test_a_bad_date_is_refused_on_the_field(
    client: APIClient, me: tuple[User, str], wallet: Account, date: Any, code: str
) -> None:
    response = add_transaction(client, me[1], wallet.id, date=date)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, "date")


@pytest.mark.parametrize(
    "date", ["2000-01-01", "2099-12-31", "2026-02-28", "2028-02-29"]
)
def test_the_dates_of_the_range_are_accepted(
    client: APIClient, me: tuple[User, str], wallet: Account, date: str
) -> None:
    assert add_transaction(client, me[1], wallet.id, date=date).status_code == 201


def test_a_date_in_the_far_future_is_allowed(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    """There is no limit relative to today: a planned payment is a valid record."""
    assert (
        add_transaction(client, me[1], wallet.id, date="2099-01-01").status_code == 201
    )


@pytest.mark.parametrize(
    ("note", "code"),
    [
        ("x" * 101, "max_length"),
        ("Line\nbreak", "invalid_characters"),
        ("Evil\u202eone", "invalid_characters"),
        (12, "invalid"),
        (None, "null"),
    ],
)
def test_a_bad_note_is_refused(
    client: APIClient, me: tuple[User, str], wallet: Account, note: Any, code: str
) -> None:
    response = add_transaction(client, me[1], wallet.id, note=note)

    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, "note")


def test_a_note_of_100_characters_is_the_longest(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    assert add_transaction(client, me[1], wallet.id, note="x" * 100).status_code == 201


@pytest.mark.parametrize(
    "field", ["id", "version", "created_at", "updated_at", "owner", "foo"]
)
def test_a_field_that_cannot_be_written_is_400_read_only(
    client: APIClient, me: tuple[User, str], wallet: Account, field: str
) -> None:
    response = add_transaction(client, me[1], wallet.id, **{field: "x"})

    error = first_error(response)
    assert (error["code"], error["attr"]) == ("read_only", field)
    assert not Transaction.objects.exists()


# --- the category and the account -------------------------------------------------------------


def test_a_custom_category_of_the_person_can_be_used(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    pets = Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")

    response = add_transaction(client, me[1], wallet.id, category_id=str(pets.id))

    assert response.status_code == 201
    assert response.json()["category_id"] == str(pets.id)


@pytest.mark.parametrize(
    ("kind", "key", "allowed"),
    [
        ("expense", "groceries", True),
        ("income", "groceries", False),
        ("income", "scholarship", True),
        ("expense", "scholarship", False),
        ("expense", "other", True),
        ("income", "other", True),
    ],
)
def test_the_category_must_allow_the_kind(
    client: APIClient,
    me: tuple[User, str],
    wallet: Account,
    kind: str,
    key: str,
    allowed: bool,
) -> None:
    response = add_transaction(
        client, me[1], wallet.id, kind=kind, category_id=built_in_id(key)
    )

    if allowed:
        assert response.status_code == 201
    else:
        assert response.status_code == 400
        error = first_error(response)
        assert (error["code"], error["attr"]) == ("category_not_allowed", "category_id")
        assert not Transaction.objects.exists()


def test_an_unknown_id_and_another_persons_id_give_the_same_answer(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], wallet: Account
) -> None:
    theirs_account = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    theirs_category = Category.objects.create(
        owner=other[0], name="Theirs", icon="gifts", kind="both"
    )
    unknown = "0198f2a4-7c3e-7b1d-9a52-3e6f1d0c4b21"

    answers = [
        add_transaction(client, me[1], wallet.id, category_id=unknown),
        add_transaction(client, me[1], wallet.id, category_id=str(theirs_category.id)),
        add_transaction(client, me[1], unknown),
        add_transaction(client, me[1], theirs_account.id),
    ]

    for answer, attr in zip(
        answers, ["category_id", "category_id", "account_id", "account_id"], strict=True
    ):
        assert answer.status_code == 400
        error = first_error(answer)
        assert (error["code"], error["attr"], error["detail"]) == (
            "does_not_exist",
            attr,
            "This id does not exist.",
        )
    assert answers[0].json() == answers[1].json()
    assert answers[2].json() == answers[3].json()
    assert not Transaction.objects.exists()


def test_both_ids_wrong_are_both_reported(
    client: APIClient, me: tuple[User, str]
) -> None:
    unknown = "0198f2a4-7c3e-7b1d-9a52-3e6f1d0c4b21"

    response = add_transaction(client, me[1], unknown, category_id=unknown)

    attrs = {e["attr"] for e in response.json()["errors"]}
    assert attrs == {"category_id", "account_id"}


@pytest.mark.parametrize("field", ["category_id", "account_id"])
def test_an_id_that_is_not_a_uuid_is_invalid(
    client: APIClient, me: tuple[User, str], wallet: Account, field: str
) -> None:
    response = add_transaction(client, me[1], wallet.id, **{field: "not-an-id"})

    error = first_error(response)
    assert (error["code"], error["attr"]) == ("invalid", field)


# --- the limit --------------------------------------------------------------------------------


def test_the_limit_is_100_000_transactions_per_person() -> None:
    assert MAX_TRANSACTIONS == 100_000


def test_the_next_transaction_over_the_limit_is_409_and_the_limit_is_per_person(
    client: APIClient,
    me: tuple[User, str],
    other: tuple[User, str],
    wallet: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ledger.services.MAX_TRANSACTIONS", 3)
    for _ in range(3):
        stored(wallet)
    theirs = Account.objects.create(owner=other[0], name="Theirs", kind="cash")

    over = add_transaction(client, me[1], wallet.id)
    fine = add_transaction(client, other[1], theirs.id)

    assert over.status_code == 409
    error = first_error(over)
    assert (error["code"], error["attr"]) == ("transaction_limit_reached", None)
    assert Transaction.objects.filter(owner=me[0]).count() == 3
    assert fine.status_code == 201


# --- reading ----------------------------------------------------------------------------------


def test_a_transaction_is_read_with_its_etag(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet, note="Lunch")

    response = get(client, transaction_url(record.id), me[1])

    assert response.status_code == 200
    assert response["ETag"] == '"1"'
    assert response.json()["note"] == "Lunch"
    assert response.json()["amount"] == "12.50"


# --- changing ---------------------------------------------------------------------------------


def test_a_change_raises_the_version_and_answers_the_new_etag(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)

    response = patch(
        client,
        transaction_url(record.id),
        me[1],
        {"amount": "13.00", "note": "Weekly shop and bread"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response["ETag"] == '"2"'
    body = response.json()
    assert (body["amount"], body["note"], body["version"], body["currency"]) == (
        "13.00",
        "Weekly shop and bread",
        2,
        "USD",
    )
    record.refresh_from_db()
    assert (record.amount, record.version) == (1300, 2)


def test_the_amount_of_a_change_is_read_in_the_stored_currency(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    yen = stored(wallet, currency="JPY", amount=1200)

    bad = patch(client, transaction_url(yen.id), me[1], {"amount": "12.00"})
    good = patch(client, transaction_url(yen.id), me[1], {"amount": "1500"})

    assert first_error(bad)["code"] == "invalid_amount"
    assert (good.status_code, good.json()["amount"]) == (200, "1500")


@pytest.mark.parametrize("value", ["USD", "EUR"])
def test_the_currency_cannot_be_sent_at_all(
    client: APIClient, me: tuple[User, str], wallet: Account, value: str
) -> None:
    record = stored(wallet)

    response = patch(client, transaction_url(record.id), me[1], {"currency": value})

    error = first_error(response)
    assert (error["code"], error["attr"]) == ("read_only", "currency")
    record.refresh_from_db()
    assert (record.currency, record.version) == ("USD", 1)


@pytest.mark.parametrize(
    "field", ["id", "version", "created_at", "updated_at", "owner", "foo"]
)
def test_any_other_field_that_cannot_be_written_is_400_read_only(
    client: APIClient, me: tuple[User, str], wallet: Account, field: str
) -> None:
    record = stored(wallet)

    response = patch(client, transaction_url(record.id), me[1], {field: "x"})

    assert first_error(response)["code"] == "read_only"


def test_a_change_of_the_kind_checks_the_category_again(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)  # an expense in groceries

    refused = patch(client, transaction_url(record.id), me[1], {"kind": "income"})
    ok = patch(
        client,
        transaction_url(record.id),
        me[1],
        {"kind": "income", "category_id": built_in_id("scholarship")},
    )

    assert first_error(refused)["code"] == "category_not_allowed"
    assert (ok.status_code, ok.json()["kind"]) == (200, "income")


def test_a_change_of_the_category_checks_the_kind(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)

    refused = patch(
        client,
        transaction_url(record.id),
        me[1],
        {"category_id": built_in_id("scholarship")},
    )
    ok = patch(
        client,
        transaction_url(record.id),
        me[1],
        {"category_id": built_in_id("dining")},
    )

    assert first_error(refused)["code"] == "category_not_allowed"
    assert ok.json()["category_id"] == built_in_id("dining")


def test_a_change_of_the_account_must_name_the_persons_own(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)
    purse = Account.objects.create(owner=me[0], name="Purse", kind="cash")
    theirs = Account.objects.create(owner=other[0], name="Theirs", kind="cash")

    ok = patch(client, transaction_url(record.id), me[1], {"account_id": str(purse.id)})
    foreign = patch(
        client, transaction_url(record.id), me[1], {"account_id": str(theirs.id)}
    )

    assert ok.json()["account_id"] == str(purse.id)
    assert first_error(foreign)["code"] == "does_not_exist"
    record.refresh_from_db()
    assert record.account_id == purse.id


def test_a_change_of_the_date_is_range_checked(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)

    response = patch(client, transaction_url(record.id), me[1], {"date": "2100-01-01"})

    assert first_error(response)["code"] == "date_out_of_range"


def test_a_change_that_changes_nothing_keeps_the_version(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)

    same = patch(
        client,
        transaction_url(record.id),
        me[1],
        {"amount": "12.50", "kind": "expense", "date": "2026-10-24"},
    )
    empty = patch(client, transaction_url(record.id), me[1], {})

    assert same.json()["version"] == empty.json()["version"] == 1


def test_a_stale_change_is_412_and_changes_nothing(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)
    patch(client, transaction_url(record.id), me[1], {"note": "second"})

    stale = patch(
        client,
        transaction_url(record.id),
        me[1],
        {"note": "third"},
        **{"If-Match": '"1"'},
    )

    assert stale.status_code == 412
    assert first_error(stale)["code"] == "precondition_failed"
    record.refresh_from_db()
    assert (record.note, record.version) == ("second", 2)


def test_put_is_not_offered(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)

    response = client.put(
        transaction_url(record.id), transaction_body(wallet.id), headers=bearer(me[1])
    )

    assert response.status_code == 405


# --- deleting ---------------------------------------------------------------------------------


def test_a_transaction_is_deleted_for_good_and_a_repeat_is_404(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)

    first = delete(client, transaction_url(record.id), me[1], **{"If-Match": '"1"'})
    again = delete(client, transaction_url(record.id), me[1])

    assert first.status_code == 204
    assert first.content == b""
    assert again.status_code == 404
    assert first_error(again)["code"] == "not_found"
    assert not Transaction.objects.exists()


def test_a_stale_delete_is_412_and_deletes_nothing(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    record = stored(wallet)
    patch(client, transaction_url(record.id), me[1], {"note": "x"})

    stale = delete(client, transaction_url(record.id), me[1], **{"If-Match": '"1"'})

    assert stale.status_code == 412
    assert Transaction.objects.filter(pk=record.pk).exists()


# --- ownership and access ---------------------------------------------------------------------


def test_another_persons_transaction_is_404_for_every_method(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    theirs_account = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    theirs = stored(theirs_account)
    mine = APIClient()
    mine.raise_request_exception = False
    mine.credentials(HTTP_AUTHORIZATION=f"Bearer {me[1]}")

    assert_hidden_from_other_users(
        mine, transaction_url(theirs.id), methods=("get", "patch", "delete")
    )

    theirs.refresh_from_db()
    assert (theirs.note, theirs.version) == ("", 1)


def test_every_operation_needs_a_signed_in_person(client: APIClient) -> None:
    some = "0198f2b0-4e5a-7d12-a3c4-6b7e8f9a0c1d"

    assert client.get(TRANSACTIONS).status_code == 401
    assert client.post(TRANSACTIONS, {}).status_code == 401
    assert client.get(transaction_url(some)).status_code == 401
    assert client.patch(transaction_url(some), {}).status_code == 401
    assert client.delete(transaction_url(some)).status_code == 401
