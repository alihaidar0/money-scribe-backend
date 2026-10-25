"""What the database itself refuses, whatever the API does (the rules of ``ledger.models``)."""

import pytest
from django.db import DataError, IntegrityError, transaction

from ledger.models import Account, Category
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
