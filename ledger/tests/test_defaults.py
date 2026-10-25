"""The two accounts a new person starts with, and the data the migration leaves behind (``docs/api-fields.md``)."""

import importlib
from datetime import timedelta
from typing import Any

import pytest
from django.apps import apps
from django.dispatch import receiver
from rest_framework.test import APIClient

from ledger import services
from ledger.models import Account, Category
from ledger.tests.helpers import ACCOUNTS, account_url, add_account, delete, get, patch
from users import signals
from users.models import EmailCode, User
from users.tests.factories import UserFactory, VerifiedUserFactory
from users.tests.helpers import AUTH, device, emailed_code, first_error

pytestmark = pytest.mark.django_db

EMAIL = "sara@example.com"
PASSWORD = "a-fine-passphrase-7"


@pytest.fixture
def client(api_client: APIClient) -> APIClient:
    api_client.raise_request_exception = False
    return api_client


def register_and_verify(client: APIClient, **extra: Any) -> Any:
    client.post(
        f"{AUTH}/register/",
        {"name": "Sara", "email": EMAIL, "password": PASSWORD, **extra},
    )
    return client.post(
        f"{AUTH}/verify-email/",
        {"email": EMAIL, "code": emailed_code(), "device": device()},
    )


# --- when an email is verified ----------------------------------------------------------------


def test_verifying_the_email_makes_a_card_and_a_cash_account(client: APIClient) -> None:
    response = register_and_verify(client, language="en")

    assert response.status_code == 200
    token = response.json()["access_token"]
    listed = get(client, ACCOUNTS, token).json()["results"]
    assert [(a["name"], a["kind"]) for a in listed] == [
        ("Card", "card"),
        ("Cash", "cash"),
    ]
    assert all(a["version"] == 1 for a in listed)


def test_the_names_are_arabic_for_an_arabic_account(client: APIClient) -> None:
    response = register_and_verify(client, language="ar")

    listed = get(client, ACCOUNTS, response.json()["access_token"]).json()["results"]

    assert [(a["name"], a["kind"]) for a in listed] == [
        ("بطاقة", "card"),
        ("نقدًا", "cash"),
    ]


def test_the_card_is_always_before_the_cash_a_millisecond_earlier(
    client: APIClient,
) -> None:
    register_and_verify(client)
    user = User.objects.get(email=EMAIL)

    card, cash = Account.objects.filter(owner=user).order_by("created_at", "id")

    assert (card.name, cash.name) == ("Card", "Cash")
    assert cash.created_at - card.created_at == timedelta(milliseconds=1)


@pytest.mark.parametrize("run", range(20))
def test_the_order_does_not_depend_on_the_clock(run: int) -> None:
    user = VerifiedUserFactory.create()

    services.create_default_accounts(user)

    ordered = Account.objects.filter(owner=user).order_by("created_at", "id")
    assert [a.name for a in ordered] == ["Card", "Cash"]


def test_the_defaults_are_ordinary_accounts_that_count_towards_the_limit(
    client: APIClient,
) -> None:
    token = register_and_verify(client).json()["access_token"]
    accounts = get(client, ACCOUNTS, token).json()["results"]

    renamed = patch(client, account_url(accounts[0]["id"]), token, {"name": "Main"})
    removed = delete(client, account_url(accounts[1]["id"]), token)
    for index in range(19):  # 1 left + 19 = 20
        assert add_account(client, token, name=f"Extra {index}").status_code == 201
    over = add_account(client, token, name="Too many")

    assert (renamed.status_code, removed.status_code) == (200, 204)
    assert first_error(over)["code"] == "account_limit_reached"


def test_a_person_who_has_accounts_gets_no_more() -> None:
    user = VerifiedUserFactory.create()
    Account.objects.create(owner=user, name="Mine", kind="other")

    created = services.create_default_accounts(user)

    assert created == []
    assert list(Account.objects.filter(owner=user).values_list("name", flat=True)) == [
        "Mine"
    ]


def test_an_unknown_language_gets_the_english_names() -> None:
    user = VerifiedUserFactory.create(language="fr")

    services.create_default_accounts(user)

    assert [
        a.name for a in Account.objects.filter(owner=user).order_by("created_at")
    ] == [
        "Card",
        "Cash",
    ]


def test_the_signal_is_sent_once_with_the_verified_user(client: APIClient) -> None:
    seen: list[User] = []

    @receiver(signals.email_verified, weak=False)
    def spy(sender: Any, user: User, **kwargs: Any) -> None:
        seen.append(user)

    try:
        register_and_verify(client)
    finally:
        signals.email_verified.disconnect(spy)

    assert [u.email for u in seen] == [EMAIL]


def test_a_failure_of_the_receiver_rolls_the_verification_back(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post(
        f"{AUTH}/register/", {"name": "Sara", "email": EMAIL, "password": PASSWORD}
    )
    code = emailed_code()

    def broken(user: User) -> None:
        raise RuntimeError("no accounts today")

    monkeypatch.setattr(services, "create_default_accounts", broken)
    body = {"email": EMAIL, "code": code, "device": device()}

    failed = client.post(f"{AUTH}/verify-email/", body)

    assert failed.status_code == 500
    user = User.objects.get(email=EMAIL)
    assert user.email_verified_at is None
    assert EmailCode.objects.filter(user=user).exists()  # the code is not spent
    monkeypatch.undo()
    retried = client.post(f"{AUTH}/verify-email/", body)
    assert retried.status_code == 200
    assert Account.objects.filter(owner=user).count() == 2


def test_changing_the_email_does_not_make_accounts(client: APIClient) -> None:
    from users.tests.helpers import PASSWORD as USER_PASSWORD
    from users.tests.helpers import bearer, start_session

    user, body = start_session(client)  # a verified user with no accounts
    token = body["access_token"]
    client.post(
        f"{AUTH}/email-change/",
        {"email": "new.address@example.com", "current_password": USER_PASSWORD},
        headers=bearer(token),
    )
    client.post(
        f"{AUTH}/email-change/confirm/",
        {"code": emailed_code()},
        headers=bearer(token),
    )

    assert not Account.objects.filter(owner=user).exists()


# --- the data migration -----------------------------------------------------------------------

migration = importlib.import_module(
    "ledger.migrations.0002_builtin_categories_and_default_accounts"
)


def test_the_migration_gives_the_defaults_to_verified_people_who_have_none() -> None:
    english = VerifiedUserFactory.create(language="en")
    arabic = VerifiedUserFactory.create(language="ar")
    has_one = VerifiedUserFactory.create()
    Account.objects.create(owner=has_one, name="Mine", kind="cash")
    unverified = UserFactory.create()

    migration.add_default_accounts(apps, None)

    def names_of(user: User) -> list[str]:
        ordered = Account.objects.filter(owner=user).order_by("created_at", "id")
        return [a.name for a in ordered]

    assert names_of(english) == ["Card", "Cash"]
    assert names_of(arabic) == ["بطاقة", "نقدًا"]
    assert names_of(has_one) == ["Mine"]
    assert names_of(unverified) == []


def test_running_the_migration_again_changes_nothing() -> None:
    user = VerifiedUserFactory.create()
    migration.add_default_accounts(apps, None)
    migration.add_default_accounts(apps, None)
    migration.add_built_in_categories(apps, None)

    assert Account.objects.filter(owner=user).count() == 2
    assert Category.objects.filter(owner__isnull=True).count() == 7


def test_reversing_the_migration_removes_only_the_built_in_categories() -> None:
    user = VerifiedUserFactory.create()
    Account.objects.create(owner=user, name="Mine", kind="cash")
    custom = Category.objects.create(owner=user, name="Pets", icon="gifts", kind="both")

    migration.remove_built_in_categories(apps, None)

    assert not Category.objects.filter(owner__isnull=True).exists()
    assert Category.objects.filter(pk=custom.pk).exists()
    assert Account.objects.filter(owner=user).count() == 1
    migration.add_built_in_categories(
        apps, None
    )  # restore for the other tests of this run
