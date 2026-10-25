"""What the database itself refuses, whatever the API does (the rules of ``ledger.models``)."""

import pytest
from django.db import DataError, IntegrityError, transaction

from ledger.models import Account, Category, Transaction
from users.tests.factories import VerifiedUserFactory

pytestmark = pytest.mark.django_db


def refused(**fields: object) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        Category.objects.create(**fields)


def test_an_account_name_is_unique_per_owner_in_any_case() -> None:
    user = VerifiedUserFactory.create()
    other = VerifiedUserFactory.create()
    Account.objects.create(owner=user, name="Wallet", kind="cash")

    with pytest.raises(IntegrityError), transaction.atomic():
        Account.objects.create(owner=user, name="WALLET", kind="cash")
    Account.objects.create(owner=other, name="Wallet", kind="cash")  # another person

    assert Account.objects.count() == 2


def test_an_account_name_cannot_be_empty() -> None:
    user = VerifiedUserFactory.create()

    with pytest.raises(IntegrityError), transaction.atomic():
        Account.objects.create(owner=user, name="", kind="cash")


def test_a_custom_category_is_always_of_the_kind_both() -> None:
    user = VerifiedUserFactory.create()

    refused(owner=user, name="Pets", icon="gifts", kind="expense")
    Category.objects.create(owner=user, name="Pets", icon="gifts", kind="both")


def test_a_category_is_built_in_or_custom_never_both_and_never_neither() -> None:
    user = VerifiedUserFactory.create()

    refused(owner=None, key=None, name="Nobody", icon="x", kind="both")  # neither
    refused(owner=user, key="mine", name="Mine", icon="x", kind="both")  # both


def test_a_built_in_key_is_unique() -> None:
    refused(owner=None, key="groceries", name="Again", icon="groceries", kind="expense")


def test_a_custom_name_is_unique_per_owner_in_any_case_but_built_in_names_are_not_constrained() -> (
    None
):
    user = VerifiedUserFactory.create()
    Category.objects.create(owner=user, name="Pets", icon="gifts", kind="both")

    refused(owner=user, name="pets", icon="gifts", kind="both")
    assert Category.objects.filter(owner__isnull=True).count() == 7


@pytest.mark.parametrize("icon", ["Bad Icon", "UPPER", "with-dash", ""])
def test_an_icon_is_a_slug(icon: str) -> None:
    user = VerifiedUserFactory.create()

    refused(owner=user, name="Pets", icon=icon, kind="both")


def test_an_icon_is_at_most_40_characters() -> None:
    user = VerifiedUserFactory.create()

    with pytest.raises(DataError), transaction.atomic():
        Category.objects.create(owner=user, name="Pets", icon="x" * 41, kind="both")


def test_a_category_name_cannot_be_empty() -> None:
    user = VerifiedUserFactory.create()

    refused(owner=user, name="", icon="gifts", kind="both")


def test_deleting_a_person_deletes_their_accounts_and_custom_categories_but_not_the_built_in_ones() -> (
    None
):
    user = VerifiedUserFactory.create()
    Account.objects.create(owner=user, name="Wallet", kind="cash")
    Category.objects.create(owner=user, name="Pets", icon="gifts", kind="both")

    user.delete()

    assert not Account.objects.exists()
    assert not Category.objects.filter(owner__isnull=False).exists()
    assert Category.objects.filter(owner__isnull=True).count() == 7


def test_the_version_rises_with_every_save() -> None:
    user = VerifiedUserFactory.create()
    account = Account.objects.create(owner=user, name="Wallet", kind="cash")

    account.name = "Purse"
    account.save()

    assert account.version == 2


# --- transactions -----------------------------------------------------------------------------


def _transaction(**fields: object) -> Transaction:
    user = VerifiedUserFactory.create()
    account = Account.objects.create(owner=user, name="Wallet", kind="cash")
    values: dict[str, object] = {
        "owner": user,
        "kind": "expense",
        "amount": 1250,
        "currency": "USD",
        "date": "2026-10-24",
        "category": Category.objects.get(key="groceries"),
        "account": account,
    }
    return Transaction(**{**values, **fields})


def test_a_valid_transaction_saves_and_its_money_is_exact() -> None:
    record = _transaction()
    record.save()

    assert record.money.to_string() == "12.50"
    assert record.note == ""


@pytest.mark.parametrize("amount", [0, -1, 1_000_000_000_000_000])
def test_the_amount_is_positive_and_within_the_largest_amount(amount: int) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _transaction(amount=amount).save()


def test_the_largest_amount_is_allowed() -> None:
    _transaction(amount=999_999_999_999_999).save()


@pytest.mark.parametrize("currency", ["usd", "XXX", "US", "US$"])
def test_the_currency_is_one_of_the_supported_ones(currency: str) -> None:
    with pytest.raises((IntegrityError, DataError)), transaction.atomic():
        _transaction(currency=currency).save()


@pytest.mark.parametrize("date", ["1999-12-31", "2100-01-01"])
def test_the_date_is_within_the_range(date: str) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _transaction(date=date).save()


def test_the_kind_is_known() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        _transaction(kind="unknown").save()


def test_a_note_of_more_than_100_characters_does_not_fit() -> None:
    with pytest.raises(DataError), transaction.atomic():
        _transaction(note="x" * 101).save()
