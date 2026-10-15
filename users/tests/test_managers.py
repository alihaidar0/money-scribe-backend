import pytest
from django.contrib.auth import authenticate

from users.models import User
from users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_create_user_normalizes_and_hashes() -> None:
    user = User.objects.create_user(
        "  Mixed.Case@Example.COM ", "  Sara Test  ", "valid-pass-1"
    )

    assert user.email == "mixed.case@example.com"
    assert user.name == "Sara Test"
    assert "valid-pass-1" not in user.password
    assert user.check_password("valid-pass-1")
    assert not user.is_staff
    assert not user.is_superuser
    assert user.is_active
    assert user.email_verified_at is None


def test_create_user_without_a_password_has_an_unusable_one() -> None:
    user = User.objects.create_user("nopass@example.com", "No Pass")

    assert not user.has_usable_password()


@pytest.mark.parametrize("email", ["", "   "])
def test_create_user_requires_an_email(email: str) -> None:
    with pytest.raises(ValueError, match="email"):
        User.objects.create_user(email, "Someone", "valid-pass-1")


@pytest.mark.parametrize("name", ["", "   "])
def test_create_user_requires_a_name(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        User.objects.create_user("someone@example.com", name, "valid-pass-1")


def test_create_superuser_sets_the_flags() -> None:
    user = User.objects.create_superuser("admin@example.com", "Admin", "valid-pass-1")

    assert user.is_staff
    assert user.is_superuser


@pytest.mark.parametrize("flag", ["is_staff", "is_superuser"])
def test_create_superuser_refuses_a_missing_flag(flag: str) -> None:
    with pytest.raises(ValueError, match=flag):
        User.objects.create_superuser(
            "admin@example.com", "Admin", "valid-pass-1", **{flag: False}
        )


def test_get_by_natural_key_ignores_case_and_spaces() -> None:
    user = UserFactory.create(email="lookup@example.com")

    assert User.objects.get_by_natural_key("  LOOKUP@Example.com ") == user


def test_sign_in_works_with_any_casing_of_the_email() -> None:
    user = UserFactory.create(email="signin@example.com", password="valid-pass-1")

    assert authenticate(username="SignIn@Example.com", password="valid-pass-1") == user
    assert authenticate(username="signin@example.com", password="wrong-pass-1") is None
