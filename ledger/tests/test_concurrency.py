"""Two requests at once on the ledger: no limit passed, no transaction left without its account or category."""

import importlib
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

import pytest
from django.apps import apps
from django.db import connections
from rest_framework.test import APIClient

from ledger import services
from ledger.models import Account, Category, Transaction
from ledger.tests.helpers import (
    ACCOUNTS,
    TRANSACTIONS,
    account_url,
    built_in_id,
    category_url,
    fresh_key,
    transaction_body,
)
from users.models import User
from users.tests.helpers import bearer, start_session

pytestmark = pytest.mark.django_db(transaction=True)

builtin = importlib.import_module(
    "ledger.migrations.0002_builtin_categories_and_default_accounts"
)


@pytest.fixture(autouse=True)
def _built_in_categories(db: None) -> None:
    """A test with real threads flushes every table when it ends, and with them the rows the migration made."""
    builtin.add_built_in_categories(apps, None)


class Answer(NamedTuple):
    status: int
    body: Any


def call(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
    **headers: str,
) -> Answer:
    """One request from its own client and database connection, as a second device would make it."""
    client = APIClient()
    client.raise_request_exception = False
    try:
        response = (
            getattr(client, method)(url, body, headers={**bearer(token), **headers})
            if body is not None
            else getattr(client, method)(url, headers={**bearer(token), **headers})
        )
        return Answer(
            response.status_code, response.json() if response.content else None
        )
    finally:
        connections.close_all()


def create(token: str, body: dict[str, Any]) -> Answer:
    return call("post", TRANSACTIONS, token, body, **{"Idempotency-Key": fresh_key()})


@pytest.fixture
def me() -> tuple[User, str]:
    user, body = start_session(APIClient())
    return user, str(body["access_token"])


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


def slowed(monkeypatch: pytest.MonkeyPatch, name: str, seconds: float = 0.5) -> None:
    """Make a service step take a moment, so the two requests really overlap."""
    original: Callable[..., Any] = getattr(services, name)

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(services, name, slow)


def together(first: Callable[[], Answer], second: Callable[[], Answer]) -> list[Answer]:
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(first)
        time.sleep(0.15)
        b = pool.submit(second)
        return [a.result(), b.result()]


def test_two_creates_at_the_limit_have_one_winner_and_one_409(
    me: tuple[User, str], wallet: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "MAX_TRANSACTIONS", 2)
    Transaction.objects.create(
        owner=me[0],
        kind="expense",
        amount=100,
        currency="USD",
        date="2026-10-01",
        category=Category.objects.get(key="other"),
        account=wallet,
    )
    slowed(monkeypatch, "resolve_references")
    body = transaction_body(wallet.id)

    answers = together(lambda: create(me[1], body), lambda: create(me[1], body))

    assert sorted(a.status for a in answers) == [201, 409]
    assert Transaction.objects.count() == 2


def test_two_accounts_at_the_limit_have_one_winner_and_one_409(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "MAX_ACCOUNTS", 1)
    slowed(monkeypatch, "ensure_account_name_free")

    def add(name: str) -> Answer:
        return call(
            "post",
            ACCOUNTS,
            me[1],
            {"name": name, "kind": "cash"},
            **{"Idempotency-Key": fresh_key()},
        )

    answers = together(lambda: add("First"), lambda: add("Second"))

    assert sorted(a.status for a in answers) == [201, 409]
    assert Account.objects.count() == 1


def test_a_transaction_cannot_be_added_to_an_account_that_is_being_deleted(
    me: tuple[User, str], wallet: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    slowed(monkeypatch, "delete_account")

    answers = together(
        lambda: call("delete", account_url(wallet.id), me[1]),
        lambda: create(me[1], transaction_body(wallet.id)),
    )

    assert answers[0].status == 204
    assert answers[1].status == 400
    assert answers[1].body["errors"][0]["code"] == "does_not_exist"
    assert not Transaction.objects.exists()


def test_an_account_cannot_be_deleted_while_a_transaction_is_being_added_to_it(
    me: tuple[User, str], wallet: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    slowed(
        monkeypatch, "ensure_category_allows"
    )  # inside the create, after the account is locked

    answers = together(
        lambda: create(me[1], transaction_body(wallet.id)),
        lambda: call("delete", account_url(wallet.id), me[1]),
    )

    assert answers[0].status == 201
    assert answers[1].status == 409
    assert answers[1].body["errors"][0]["code"] == "account_has_transactions"
    assert Account.objects.filter(pk=wallet.pk).exists()
    assert Transaction.objects.count() == 1


def test_a_transaction_cannot_be_added_to_a_category_that_is_being_deleted(
    me: tuple[User, str], wallet: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    pets = Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")
    slowed(monkeypatch, "delete_category")

    answers = together(
        lambda: call("delete", category_url(pets.id), me[1]),
        lambda: create(me[1], transaction_body(wallet.id, category_id=str(pets.id))),
    )

    assert answers[0].status == 204
    assert answers[1].status == 400
    assert answers[1].body["errors"][0]["code"] == "does_not_exist"
    assert not Transaction.objects.exists()


def test_a_transaction_added_while_its_category_is_deleted_ends_up_in_other(
    me: tuple[User, str], wallet: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    pets = Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")
    slowed(
        monkeypatch, "ensure_category_allows"
    )  # inside the create, after the category is locked

    answers = together(
        lambda: create(me[1], transaction_body(wallet.id, category_id=str(pets.id))),
        lambda: call("delete", category_url(pets.id), me[1]),
    )

    assert [a.status for a in answers] == [201, 204]
    record = Transaction.objects.get()
    assert str(record.category_id) == built_in_id("other")
    assert not Category.objects.filter(pk=pets.pk).exists()
