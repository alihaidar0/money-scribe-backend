import json
import logging
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
import requests
from django.core.mail import send_mail
from pytest_django.fixtures import Settings

from outbox.checks import OUTBOX_BACKEND, RESEND_BACKEND, check_resend_key
from users.emails import send_account_exists_notice

FAKE_KEY = "re_not-a-real-key-for-tests"


@pytest.fixture
def resend(settings: Settings) -> None:
    settings.EMAIL_BACKEND = RESEND_BACKEND
    settings.ANYMAIL = {"RESEND_API_KEY": FAKE_KEY, "REQUESTS_TIMEOUT": 10}
    settings.DEFAULT_FROM_EMAIL = "Money Scribe <noreply@mail.example.com>"


def _response(status: int, body: dict[str, Any]) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


@pytest.fixture
def api() -> Iterator[mock.MagicMock]:
    """Stand in for the HTTP call, so no test reaches the real service."""
    with mock.patch.object(
        requests.Session, "request", return_value=_response(200, {"id": "email-1"})
    ) as request:
        yield request


# --- the request sent to the service ------------------------------------------------------------


@pytest.mark.usefixtures("resend")
def test_an_email_is_posted_to_resend_with_the_key(api: mock.MagicMock) -> None:
    sent = send_mail("Your code", "Your code is 4821.", None, ["sara@example.com"])

    assert sent == 1
    api.assert_called_once()
    call = api.call_args.kwargs
    assert call["method"] == "POST"
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert call["timeout"] == 10
    payload = json.loads(call["data"])
    assert payload["from"] == "Money Scribe <noreply@mail.example.com>"
    assert payload["to"] == ["sara@example.com"]
    assert payload["subject"] == "Your code"
    assert payload["text"] == "Your code is 4821."


@pytest.mark.usefixtures("resend")
def test_a_refused_email_does_not_break_the_caller_and_leaks_nothing(
    api: mock.MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    api.return_value = _response(403, {"message": "domain is not verified"})

    with caplog.at_level(logging.DEBUG):
        send_account_exists_notice(email="sara@example.com", language="en")

    api.assert_called_once()
    assert "Sending an account notice failed." in caplog.text
    assert "sara@example.com" not in caplog.text
    assert FAKE_KEY not in caplog.text


# --- the key check ------------------------------------------------------------------------------


def test_the_resend_backend_without_a_key_is_refused(settings: Settings) -> None:
    settings.EMAIL_BACKEND = RESEND_BACKEND
    settings.ANYMAIL = {"RESEND_API_KEY": ""}

    assert [error.id for error in check_resend_key(None)] == ["outbox.E003"]


@pytest.mark.usefixtures("resend")
def test_the_resend_backend_with_a_key_passes() -> None:
    assert check_resend_key(None) == []


@pytest.mark.parametrize(
    "backend",
    [
        OUTBOX_BACKEND,
        "django.core.mail.backends.smtp.EmailBackend",
        "django.core.mail.backends.locmem.EmailBackend",
    ],
)
def test_another_backend_needs_no_key(settings: Settings, backend: str) -> None:
    settings.EMAIL_BACKEND = backend
    settings.ANYMAIL = {"RESEND_API_KEY": ""}

    assert check_resend_key(None) == []
