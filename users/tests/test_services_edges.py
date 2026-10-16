"""The rare branches of the authentication services: races, a failing mail server, odd leftovers."""

import logging
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.db import IntegrityError
from django.utils import timezone
from rest_framework.test import APIClient

from conftest import Clock
from users import limits, services, tokens
from users.models import EmailCode, EndReason, Session, User
from users.tests.factories import UserFactory
from users.tests.helpers import (
    AUTH,
    PASSWORD,
    device,
    first_error,
    refresh,
    start_session,
)

pytestmark = pytest.mark.django_db

EMAIL = "sara@example.com"


def test_a_registration_that_loses_a_race_for_the_address_answers_like_any_other(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def lose_the_race(*args: Any, **kwargs: Any) -> None:
        raise IntegrityError

    monkeypatch.setattr(User.objects, "create_user", lose_the_race)

    response = api_client.post(
        f"{AUTH}/register/",
        {"name": "Sara", "email": EMAIL, "password": "a-fine-passphrase-7"},
    )

    # The request that won the race sends the code; this one answers the same and sends nothing.
    assert response.status_code == 201
    assert response.json()["email"] == EMAIL
    assert mail.outbox == []


def test_a_failing_mail_server_does_not_fail_the_registration_and_logs_nothing_private(
    api_client: APIClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("connection refused for sara@example.com")

    monkeypatch.setattr("users.emails.send_mail", broken)

    with caplog.at_level(logging.WARNING, logger="users.emails"):
        response = api_client.post(
            f"{AUTH}/register/",
            {"name": "Sara", "email": EMAIL, "password": "a-fine-passphrase-7"},
        )

    assert response.status_code == 201
    assert "Sending a verification email failed." in caplog.text
    assert EMAIL not in caplog.text
    assert "connection refused" not in caplog.text


def test_an_unverified_account_without_any_code_answers_like_nothing_was_sent(
    api_client: APIClient,
) -> None:
    UserFactory.create(email=EMAIL)

    response = api_client.post(
        f"{AUTH}/verify-email/", {"email": EMAIL, "code": "123456", "device": device()}
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "code_expired"
    assert EmailCode.objects.count() == 0


def test_a_refresh_token_of_the_current_generation_with_another_hash_is_invalid(
    api_client: APIClient, settings: Any
) -> None:
    _, body = start_session(api_client)
    session = Session.objects.get()
    old_key = "the-old-signing-key-0123456789abcdef-0123456789"
    current_key, current_id = settings.JWT_SIGNING_KEY, settings.JWT_KEY_ID
    settings.JWT_SIGNING_KEY, settings.JWT_KEY_ID = old_key, "k0"
    older_token = tokens.issue_refresh_token(session)
    settings.JWT_SIGNING_KEY, settings.JWT_KEY_ID = current_key, current_id
    settings.JWT_PREVIOUS_KEYS = {"k0": old_key}

    # Same session and generation, signed by the previous key: it verifies but is not the token we stored.
    response = refresh(api_client, older_token)

    assert first_error(response)["code"] == "token_invalid"
    assert Session.objects.get().ended_at is None
    assert refresh(api_client, body["refresh_token"]).status_code == 200


def test_expiring_a_session_from_a_token_that_cannot_be_read_does_nothing(
    clock: Clock,
) -> None:
    services._end_expired_session("garbage", timezone.now())


def test_resend_wait_after_the_hourly_limit_is_the_long_wait(clock: Clock) -> None:
    for _ in range(5):
        limits.reserve_code_send(EMAIL)
        clock.advance(seconds=61)

    assert limits.resend_wait(EMAIL) == 3600 - 5 * 61


def test_the_models_describe_themselves(api_client: APIClient) -> None:
    user, _ = start_session(api_client)
    session = Session.objects.get()
    code = EmailCode.objects.create(
        user=user,
        purpose="verify_email",
        code_hash="h",
        expires_at=timezone.now() + timedelta(minutes=10),
    )

    assert not session.is_ended
    assert str(user.id) in str(session)
    assert "verify_email" in str(code)
    services.end_session(session, EndReason.SIGNED_OUT)
    session.refresh_from_db()
    assert session.is_ended


def test_a_correct_sign_in_password_is_the_factory_password() -> None:
    user = UserFactory.create()

    assert user.check_password(PASSWORD)
