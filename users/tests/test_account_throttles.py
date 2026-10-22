"""Every account operation has its own per-client limit, on top of the per-account ones."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from users import throttles
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import AUTH, PASSWORD, bearer, first_error, start_session

pytestmark = pytest.mark.django_db

PUBLIC = [
    (
        "password-reset/request",
        throttles.PasswordResetRequestThrottle,
        {"email": "a@example.com"},
    ),
    (
        "password-reset/verify",
        throttles.PasswordResetVerifyThrottle,
        {"email": "a@example.com", "code": "123456"},
    ),
    (
        "password-reset/confirm",
        throttles.PasswordResetConfirmThrottle,
        {"reset_token": "x", "password": "a-fine-passphrase-7"},
    ),
]
SIGNED_IN = [
    (
        "password/change",
        throttles.PasswordChangeThrottle,
        {"current_password": PASSWORD, "password": "a-fine-passphrase-7"},
    ),
    ("account/delete", throttles.AccountDeleteThrottle, {"password": "wrong"}),
    (
        "email-change",
        throttles.EmailChangeRequestThrottle,
        {"email": "new@example.com", "current_password": PASSWORD},
    ),
    ("email-change/confirm", throttles.EmailChangeConfirmThrottle, {"code": "123456"}),
]


@pytest.mark.parametrize(("path", "throttle", "body"), PUBLIC)
def test_a_client_that_asks_too_often_is_throttled(
    api_client: APIClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    throttle: Any,
    body: dict[str, str],
) -> None:
    monkeypatch.setattr(throttle, "THROTTLE_RATES", {throttle.scope: "1/hour"})
    VerifiedUserFactory.create(email="a@example.com")
    api_client.post(f"{AUTH}/{path}/", body)

    response = api_client.post(f"{AUTH}/{path}/", body)

    assert response.status_code == 429
    assert first_error(response)["code"] == "throttled"
    assert int(response["Retry-After"]) >= 1


@pytest.mark.parametrize(("path", "throttle", "body"), SIGNED_IN)
def test_a_signed_in_client_that_asks_too_often_is_throttled(
    api_client: APIClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    throttle: Any,
    body: dict[str, str],
) -> None:
    monkeypatch.setattr(throttle, "THROTTLE_RATES", {throttle.scope: "1/hour"})
    _, tokens = start_session(api_client)
    api_client.post(f"{AUTH}/{path}/", body, headers=bearer(tokens["access_token"]))

    response = api_client.post(
        f"{AUTH}/{path}/", body, headers=bearer(tokens["access_token"])
    )

    assert response.status_code == 429
    assert first_error(response)["code"] == "throttled"
    assert int(response["Retry-After"]) >= 1
