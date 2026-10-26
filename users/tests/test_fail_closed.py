"""When the limit store (Redis) is down, the calls that depend on a limit are refused, not let through."""

import pytest
from rest_framework.test import APIClient

from users import limits
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    PASSWORD,
    bearer,
    device,
    first_error,
    refresh,
    start_session,
)
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db

EMAIL = "sara@example.com"


@pytest.fixture
def redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "cache", BrokenCache())


@pytest.fixture
def client(api_client: APIClient) -> APIClient:
    """A client that hands back a 5xx response instead of re-raising it.

    The error package fires ``got_request_exception`` for every 5xx so that monitoring sees it, and
    Django's test client re-raises what that signal carries; a real client just gets the response.
    """
    api_client.raise_request_exception = False
    return api_client


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "register",
            {"name": "Sara", "email": EMAIL, "password": "a-fine-passphrase-7"},
        ),
        ("resend-code", {"email": EMAIL}),
        ("verify-email", {"email": EMAIL, "code": "123456", "device": device()}),
        ("sign-in", {"email": EMAIL, "password": PASSWORD, "device": device()}),
        ("password-reset/request", {"email": EMAIL}),
        ("password-reset/verify", {"email": EMAIL, "code": "123456"}),
        ("password-reset/confirm", {"reset_token": "x", "password": "a-fine-pass-17"}),
    ],
)
def test_the_calls_that_need_a_limit_answer_503_with_retry_after(
    client: APIClient, redis_down: None, path: str, body: dict[str, object]
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    response = client.post(f"{AUTH}/{path}/", body)

    assert response.status_code == 503
    assert response["Retry-After"] == "30"
    assert response.json()["type"] == "server_error"
    assert first_error(response)["code"] == "service_unavailable"


def test_nothing_is_created_or_sent_while_the_store_is_down(
    client: APIClient, redis_down: None
) -> None:
    from django.core import mail

    from users.models import Session, User

    client.post(
        f"{AUTH}/register/",
        {"name": "Sara", "email": EMAIL, "password": "a-fine-passphrase-7"},
    )
    VerifiedUserFactory.create(email="owner@example.com")
    client.post(
        f"{AUTH}/sign-in/",
        {"email": "owner@example.com", "password": PASSWORD, "device": device()},
    )

    assert not User.objects.filter(email=EMAIL).exists()
    assert mail.outbox == []
    assert Session.objects.count() == 0


def test_a_signed_in_user_keeps_working_while_the_store_is_down(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, body = start_session(api_client)
    monkeypatch.setattr(limits, "cache", BrokenCache())

    profile = api_client.get(f"{AUTH}/profile/", headers=bearer(body["access_token"]))
    renewed = refresh(api_client, body["refresh_token"])
    signed_out = api_client.post(
        f"{AUTH}/sign-out/", headers=bearer(renewed.json()["access_token"])
    )

    assert profile.status_code == 200
    assert renewed.status_code == 200
    assert signed_out.status_code == 204


def test_lifting_the_account_lock_after_a_reset_survives_an_outage(
    redis_down: None,
) -> None:
    """The password has already changed when the lock is lifted, so an outage there must not fail the reset."""
    limits.clear_account_sign_in_lock(EMAIL)
