"""The rules that join the accounts and the categories to the transactions (ADR 0017)."""

from typing import Any

import pytest
from django.db import transaction as db_transaction
from rest_framework.test import APIClient

from ledger.models import Account, Category, Transaction
from ledger.tests.helpers import (
    account_url,
    add_transaction,
    built_in_id,
    category_url,
    delete,
    get,
    patch,
    transaction_url,
)
from users.models import User
from users.tests.helpers import first_error

pytestmark = pytest.mark.django_db


def make(account: Account, category: Category, **fields: Any) -> Transaction:
    values: dict[str, Any] = {
        "owner": account.owner,
        "kind": "expense",
        "amount": 1250,
        "currency": "USD",
        "date": "2026-10-24",
        "category": category,
        "account": account,
        "note": "Lunch",
    }
    return Transaction.objects.create(**{**values, **fields})


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


@pytest.fixture
def pets(me: tuple[User, str]) -> Category:
    return Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")


# --- an account that has transactions ---------------------------------------------------------


def test_an_account_that_has_transactions_cannot_be_deleted(
    client: APIClient, me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    make(wallet, pets)

    response = delete(client, account_url(wallet.id), me[1])

    assert response.status_code == 409
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("account_has_transactions", None)
    assert Account.objects.filter(pk=wallet.pk).exists()
    assert Transaction.objects.count() == 1


def test_an_account_can_be_deleted_once_its_transactions_are_gone(
    client: APIClient, me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    record = make(wallet, pets)

    assert delete(client, transaction_url(record.id), me[1]).status_code == 204
    assert delete(client, account_url(wallet.id), me[1]).status_code == 204
    assert not Account.objects.filter(pk=wallet.pk).exists()


def test_another_accounts_transactions_do_not_stop_the_delete(
    client: APIClient, me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    purse = Account.objects.create(owner=me[0], name="Purse", kind="cash")
    make(purse, pets)

    assert delete(client, account_url(wallet.id), me[1]).status_code == 204


def test_a_refused_delete_leaves_the_version_alone(
    client: APIClient, me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    make(wallet, pets)

    delete(client, account_url(wallet.id), me[1])

    wallet.refresh_from_db()
    assert wallet.version == 1


def test_the_stale_version_is_checked_before_the_transactions(
    client: APIClient, me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    make(wallet, pets)
    patch(client, account_url(wallet.id), me[1], {"name": "Renamed"})

    response = delete(client, account_url(wallet.id), me[1], **{"If-Match": '"1"'})

    assert response.status_code == 412


# --- deleting a custom category moves its transactions to other -------------------------------


def test_the_transactions_of_a_deleted_category_move_to_other(
    client: APIClient, me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    spent = make(wallet, pets, kind="expense")
    earned = make(wallet, pets, kind="income", amount=5000, note="Refund")
    for record in (spent, earned):
        record.refresh_from_db()
    before = {
        t.pk: (t.amount, t.date, t.account_id, t.kind, t.note, t.currency)
        for t in (spent, earned)
    }

    response = delete(client, category_url(pets.id), me[1])

    assert response.status_code == 204
    assert not Category.objects.filter(pk=pets.pk).exists()
    for record in (spent, earned):
        record.refresh_from_db()
        assert str(record.category_id) == built_in_id("other")
        assert (
            record.amount,
            record.date,
            record.account_id,
            record.kind,
            record.note,
            record.currency,
        ) == before[record.pk]


def test_a_moved_transaction_gets_a_new_version_and_update_time(
    client: APIClient, me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    record = make(wallet, pets)
    updated_before = record.updated_at

    delete(client, category_url(pets.id), me[1])

    record.refresh_from_db()
    assert record.version == 2
    assert record.updated_at > updated_before


def test_an_edit_form_that_was_open_on_a_moved_transaction_gets_412(
    client: APIClient, me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    record = make(wallet, pets)
    opened = get(client, transaction_url(record.id), me[1])
    delete(client, category_url(pets.id), me[1])

    stale = patch(
        client,
        transaction_url(record.id),
        me[1],
        {"note": "Edited"},
        **{"If-Match": opened["ETag"]},
    )
    reloaded = get(client, transaction_url(record.id), me[1])
    retried = patch(
        client,
        transaction_url(record.id),
        me[1],
        {"note": "Edited"},
        **{"If-Match": reloaded["ETag"]},
    )

    assert stale.status_code == 412
    assert reloaded.json()["category_id"] == built_in_id("other")
    assert retried.status_code == 200


def test_other_transactions_are_not_touched(
    client: APIClient,
    me: tuple[User, str],
    other: tuple[User, str],
    wallet: Account,
    pets: Category,
) -> None:
    mine_elsewhere = make(wallet, Category.objects.get(key="dining"))
    theirs_account = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    theirs_pets = Category.objects.create(
        owner=other[0], name="Pets", icon="gifts", kind="both"
    )
    theirs = make(theirs_account, theirs_pets)
    on_other = make(wallet, Category.objects.get(key="other"))

    delete(client, category_url(pets.id), me[1])

    for record, category_id in (
        (mine_elsewhere, built_in_id("dining")),
        (theirs, str(theirs_pets.id)),
        (on_other, built_in_id("other")),
    ):
        record.refresh_from_db()
        assert (str(record.category_id), record.version) == (category_id, 1)


def test_a_category_without_transactions_is_just_deleted(
    client: APIClient, me: tuple[User, str], pets: Category
) -> None:
    assert delete(client, category_url(pets.id), me[1]).status_code == 204
    assert not Category.objects.filter(pk=pets.pk).exists()


def test_the_move_and_the_delete_are_one_transaction(
    client: APIClient,
    me: tuple[User, str],
    wallet: Account,
    pets: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = make(wallet, pets)

    def broken(self: Category, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the delete failed")

    monkeypatch.setattr(Category, "delete", broken)

    response = delete(client, category_url(pets.id), me[1])

    assert response.status_code == 500
    record.refresh_from_db()
    assert (record.category_id, record.version) == (pets.id, 1)
    assert Category.objects.filter(pk=pets.pk).exists()


def test_other_allows_both_kinds_so_a_moved_transaction_stays_valid() -> None:
    assert Category.objects.get(key="other", owner__isnull=True).kind == "both"


def test_a_built_in_category_with_transactions_is_still_not_deletable(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    make(wallet, Category.objects.get(key="groceries"))

    response = delete(client, category_url(built_in_id("groceries")), me[1])

    assert response.status_code == 403
    assert first_error(response)["code"] == "category_is_builtin"


# --- the database itself ----------------------------------------------------------------------


def test_the_database_refuses_to_delete_an_account_or_a_category_in_use(
    wallet: Account, pets: Category
) -> None:
    from django.db.models import RestrictedError

    make(wallet, pets)

    with pytest.raises(RestrictedError), db_transaction.atomic():
        wallet.delete()
    with pytest.raises(RestrictedError), db_transaction.atomic():
        pets.delete()


def test_deleting_a_person_deletes_all_their_ledger_data_in_one_go(
    me: tuple[User, str], wallet: Account, pets: Category
) -> None:
    make(wallet, pets)

    me[0].delete()

    assert not Transaction.objects.exists()
    assert not Account.objects.exists()
    assert not Category.objects.filter(owner__isnull=False).exists()
    assert Category.objects.filter(owner__isnull=True).count() == 7


def test_a_transaction_made_through_the_api_can_then_block_the_delete(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    created = add_transaction(client, me[1], wallet.id)

    assert created.status_code == 201
    assert delete(client, account_url(wallet.id), me[1]).status_code == 409
