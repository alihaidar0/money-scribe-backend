import pytest
from django.test import Client
from django.urls import reverse

from users.models import User
from users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client: Client) -> Client:
    staff = UserFactory.create(is_staff=True, is_superuser=True)
    client.force_login(staff)
    return client


def test_changelist_lists_users_by_email(staff_client: Client) -> None:
    UserFactory.create(email="listed@example.com")

    response = staff_client.get(reverse("admin:users_user_changelist"))

    assert response.status_code == 200
    assert b"listed@example.com" in response.content


def test_change_page_opens(staff_client: Client) -> None:
    user = UserFactory.create()

    response = staff_client.get(reverse("admin:users_user_change", args=[user.pk]))

    assert response.status_code == 200


def test_add_form_creates_a_user_with_a_normalized_email(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:users_user_add"),
        {
            "email": "New.Person@Example.com",
            "name": "New Person",
            "usable_password": "true",
            "password1": "a-fine-passphrase-7",
            "password2": "a-fine-passphrase-7",
        },
    )

    assert response.status_code == 302
    created = User.objects.get(email="new.person@example.com")
    assert created.name == "New Person"
    assert created.check_password("a-fine-passphrase-7")


def test_add_form_rejects_a_password_without_a_digit(staff_client: Client) -> None:
    response = staff_client.post(
        reverse("admin:users_user_add"),
        {
            "email": "weak@example.com",
            "name": "Weak Password",
            "usable_password": "true",
            "password1": "no-digits-in-here",
            "password2": "no-digits-in-here",
        },
    )

    assert response.status_code == 200
    assert not User.objects.filter(email="weak@example.com").exists()
