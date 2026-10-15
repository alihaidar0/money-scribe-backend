import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from users.models import User
from users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_primary_key_is_a_uuid() -> None:
    user = UserFactory.create()

    assert isinstance(user.pk, uuid.UUID)
    assert UserFactory.create().pk != user.pk


def test_model_is_wired_for_email_sign_in() -> None:
    assert User.USERNAME_FIELD == "email"
    assert User.EMAIL_FIELD == "email"
    assert User.REQUIRED_FIELDS == ["name"]
    assert not hasattr(User, "username")


def test_defaults() -> None:
    user = UserFactory.create()

    assert user.is_active
    assert not user.is_staff
    assert not user.is_superuser
    assert user.email_verified_at is None
    assert user.last_login is None
    assert abs((timezone.now() - user.date_joined).total_seconds()) < 60


def test_save_trims_and_lowers_the_email_and_trims_the_name() -> None:
    user = UserFactory.create(email="  Person@Example.COM  ", name="  Amira  ")
    user.refresh_from_db()

    assert user.email == "person@example.com"
    assert user.name == "Amira"


@pytest.mark.parametrize("name", ["Алексей Иванов", "أميرة خالد", "José Müller"])
def test_any_unicode_letters_are_accepted_in_the_name(name: str) -> None:
    user = UserFactory.create(name=name)
    user.refresh_from_db()

    assert user.name == name


def test_email_is_unique_regardless_of_case() -> None:
    UserFactory.create(email="same@example.com")

    with pytest.raises(IntegrityError), transaction.atomic():
        UserFactory.create(email="SAME@example.com")


def test_database_rejects_mixed_case_duplicates_written_without_save() -> None:
    UserFactory.create(email="first@example.com")
    other = UserFactory.create(email="second@example.com")

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=other.pk).update(email="FIRST@example.com")


def test_database_rejects_an_empty_name() -> None:
    user = UserFactory.create()

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(name="")


def test_name_longer_than_100_characters_fails_validation() -> None:
    user = UserFactory.build(name="x" * 101)

    with pytest.raises(ValidationError) as error:
        user.full_clean(exclude=["password"])
    assert "name" in error.value.message_dict


def test_blank_name_fails_validation() -> None:
    user = UserFactory.build(name="")

    with pytest.raises(ValidationError) as error:
        user.full_clean(exclude=["password"])
    assert "name" in error.value.message_dict


def test_str_is_the_email() -> None:
    assert str(UserFactory.create(email="shown@example.com")) == "shown@example.com"


def test_email_verification_can_be_recorded() -> None:
    user = UserFactory.create()
    user.email_verified_at = timezone.now()
    user.save()
    user.refresh_from_db()

    assert user.email_verified_at is not None
