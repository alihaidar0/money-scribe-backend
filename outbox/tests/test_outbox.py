from datetime import timedelta

import pytest
from celery.schedules import crontab
from django.core.mail import EmailMessage, send_mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pytest_django.fixtures import Settings

from outbox.checks import OUTBOX_BACKEND, check_outbox_environment
from outbox.models import OutgoingEmail
from outbox.tasks import purge_old_emails
from users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _use_the_outbox(settings: Settings) -> None:
    settings.EMAIL_BACKEND = OUTBOX_BACKEND


# --- the backend ------------------------------------------------------------------------------


def test_send_mail_saves_the_email_instead_of_sending_it() -> None:
    sent = send_mail(
        "Your code", "Your code is 4821.", "noreply@example.com", ["sara@example.com"]
    )

    assert sent == 1
    email = OutgoingEmail.objects.get()
    assert email.subject == "Your code"
    assert email.body == "Your code is 4821."
    assert email.from_email == "noreply@example.com"
    assert email.recipients == ["sara@example.com"]


def test_every_recipient_is_kept() -> None:
    EmailMessage(
        "Hello",
        "Body",
        "noreply@example.com",
        to=["a@example.com"],
        cc=["b@example.com"],
        bcc=["c@example.com"],
    ).send()

    assert OutgoingEmail.objects.get().recipients == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
    ]


def test_several_messages_are_saved_together() -> None:
    from django.core.mail import get_connection

    connection = get_connection()
    messages = [
        EmailMessage(
            f"Subject {n}", "Body", "noreply@example.com", [f"u{n}@example.com"]
        )
        for n in range(3)
    ]

    assert connection.send_messages(messages) == 3
    assert OutgoingEmail.objects.count() == 3


# --- the purge --------------------------------------------------------------------------------


def _email(subject: str, age: timedelta) -> OutgoingEmail:
    email = OutgoingEmail.objects.create(
        from_email="noreply@example.com",
        recipients=["u@example.com"],
        subject=subject,
        body="Body",
    )
    OutgoingEmail.objects.filter(pk=email.pk).update(created_at=timezone.now() - age)
    return email


def test_the_purge_deletes_only_emails_past_the_retention(settings: Settings) -> None:
    settings.OUTBOX_RETENTION_HOURS = 24
    _email("old", timedelta(hours=25))
    _email("recent", timedelta(hours=23))

    deleted = purge_old_emails()

    assert deleted == 1
    assert list(OutgoingEmail.objects.values_list("subject", flat=True)) == ["recent"]


def test_the_retention_is_a_setting(settings: Settings) -> None:
    settings.OUTBOX_RETENTION_HOURS = 1
    _email("two hours old", timedelta(hours=2))

    assert purge_old_emails() == 1


def test_the_purge_is_scheduled_daily(settings: Settings) -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["outbox-purge"]

    assert entry["task"] == purge_old_emails.name
    assert isinstance(entry["schedule"], crontab)


# --- the guard against production -------------------------------------------------------------


def test_the_outbox_is_refused_in_production(settings: Settings) -> None:
    settings.APP_ENVIRONMENT = "production"

    errors = check_outbox_environment(None)

    assert [error.id for error in errors] == ["outbox.E001"]


@pytest.mark.parametrize("environment", ["development", "staging"])
def test_the_outbox_is_allowed_where_the_data_is_invented(
    settings: Settings, environment: str
) -> None:
    settings.APP_ENVIRONMENT = environment

    assert check_outbox_environment(None) == []


def test_real_email_is_fine_in_production(settings: Settings) -> None:
    settings.APP_ENVIRONMENT = "production"
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

    assert check_outbox_environment(None) == []


@pytest.mark.parametrize("environment", ["prod", "", "Production"])
def test_a_misspelt_environment_is_an_error_so_it_cannot_bypass_the_guard(
    settings: Settings, environment: str
) -> None:
    settings.APP_ENVIRONMENT = environment

    assert [error.id for error in check_outbox_environment(None)] == ["outbox.E002"]


def test_production_settings_default_to_the_safe_environment() -> None:
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "SECRET_KEY": "x" * 50,
    }
    env.pop("APP_ENVIRONMENT", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; print(settings.APP_ENVIRONMENT)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "production"


# --- the admin --------------------------------------------------------------------------------


def _saved_email() -> OutgoingEmail:
    return OutgoingEmail.objects.create(
        from_email="noreply@example.com",
        recipients=["sara@example.com"],
        subject="Your code",
        body="Your code is 4821.",
    )


def test_a_superuser_reads_the_email_and_its_code(client: Client) -> None:
    email = _saved_email()
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))

    listing = client.get(reverse("admin:outbox_outgoingemail_changelist"))
    detail = client.get(reverse("admin:outbox_outgoingemail_change", args=[email.pk]))

    assert listing.status_code == 200
    assert b"sara@example.com" in listing.content
    assert detail.status_code == 200
    assert b"Your code is 4821." in detail.content


def test_a_superuser_can_delete_an_email_but_not_add_or_edit_one(
    client: Client,
) -> None:
    email = _saved_email()
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))

    assert client.get(reverse("admin:outbox_outgoingemail_add")).status_code == 403
    assert (
        client.post(
            reverse("admin:outbox_outgoingemail_change", args=[email.pk]),
            {"subject": "Changed"},
        ).status_code
        == 403
    )
    response = client.post(
        reverse("admin:outbox_outgoingemail_delete", args=[email.pk]), {"post": "yes"}
    )
    assert response.status_code == 302
    assert not OutgoingEmail.objects.exists()


def test_a_staff_account_that_is_not_a_superuser_cannot_see_the_outbox(
    client: Client,
) -> None:
    email = _saved_email()
    client.force_login(UserFactory.create(is_staff=True))

    index = client.get(reverse("admin:index"))
    listing = client.get(reverse("admin:outbox_outgoingemail_changelist"))
    detail = client.get(reverse("admin:outbox_outgoingemail_change", args=[email.pk]))

    assert b"outgoing emails" not in index.content.lower()
    assert listing.status_code == 403
    assert detail.status_code == 403
