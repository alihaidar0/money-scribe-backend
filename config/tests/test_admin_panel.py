import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings

from users.models import User
from users.tests.factories import UserFactory


def test_the_admin_lives_at_the_configured_path(settings: Settings) -> None:
    assert reverse("admin:index") == f"/{settings.ADMIN_URL}"
    assert settings.ADMIN_URL.endswith("/")
    assert not settings.ADMIN_URL.startswith("/")


def test_an_anonymous_visitor_is_sent_to_the_login_page(client: Client) -> None:
    response = client.get(reverse("admin:index"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("admin:login"))


def test_a_user_who_is_not_staff_cannot_use_the_admin(client: Client, db: None) -> None:
    client.force_login(UserFactory.create())

    response = client.get(reverse("admin:index"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("admin:login"))


def test_a_staff_account_opens_the_admin(client: Client, db: None) -> None:
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))

    response = client.get(reverse("admin:index"))

    assert response.status_code == 200
    assert b"Money Scribe administration" in response.content


def test_a_superuser_is_created_with_an_email_and_a_name(
    monkeypatch: pytest.MonkeyPatch, db: None
) -> None:
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "a-fine-passphrase-7")

    call_command(
        "createsuperuser",
        interactive=False,
        email="Owner@Example.com",
        name="The Owner",
    )

    owner = User.objects.get(email="owner@example.com")
    assert owner.name == "The Owner"
    assert owner.is_staff
    assert owner.is_superuser
    assert owner.check_password("a-fine-passphrase-7")
