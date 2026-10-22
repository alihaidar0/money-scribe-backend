"""The bot challenge of sign-in (ADR 0013), with a stand-in for the Turnstile service."""

import urllib.parse
from typing import Any

import pytest
from django.core import checks
from rest_framework.response import Response
from rest_framework.test import APIClient

from users import challenge
from users import checks as user_checks
from users.models import Session
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import AUTH, PASSWORD, device, first_error

pytestmark = pytest.mark.django_db

EMAIL = "sara@example.com"
SECRET = "test-secret-not-real"


class FakeTurnstile:
    """Records what the server posts and answers with ``answer``, or raises ``failure``."""

    def __init__(self) -> None:
        self.answer: dict[str, Any] = {"success": True, "action": "sign_in"}
        self.failure: Exception | None = None
        self.posted: list[dict[str, str]] = []

    def __call__(self, fields: dict[str, str]) -> dict[str, Any]:
        self.posted.append(fields)
        if self.failure is not None:
            raise self.failure
        return self.answer


@pytest.fixture
def turnstile(settings: Any, monkeypatch: pytest.MonkeyPatch) -> FakeTurnstile:
    settings.TURNSTILE_SECRET_KEY = SECRET
    fake = FakeTurnstile()
    monkeypatch.setattr(challenge, "_post", fake)
    return fake


def attempt(
    client: APIClient,
    password: str = "wrong-password-0",  # noqa: S107
    **extra: Any,
) -> Response:
    body = {"email": EMAIL, "password": password, "device": device(), **extra}
    return client.post(f"{AUTH}/sign-in/", body)


def fail_until_challenged(client: APIClient, times: int = 5) -> None:
    """Failed sign-ins for different emails, so no single account locks before the address does."""
    for n in range(times):
        attempt(client, email=f"person{n}@example.com")


def test_nobody_is_challenged_while_the_challenge_is_off(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    fail_until_challenged(api_client, 13)

    # no challenge is asked for: the 14th is a plain failure, the 15th locks the address
    assert attempt(api_client).status_code == 401
    assert attempt(api_client).status_code == 429


def test_a_client_that_has_not_failed_is_not_challenged(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    assert attempt(api_client, PASSWORD).status_code == 200
    assert turnstile.posted == []


def test_the_failures_before_the_threshold_are_answered_normally(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    fail_until_challenged(api_client, 4)

    assert first_error(attempt(api_client))["code"] == "wrong_password"


def test_after_five_failures_a_token_is_asked_for(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    for n in range(5):
        attempt(api_client, email=f"person{n}@example.com")

    response = attempt(api_client, PASSWORD)

    assert response.status_code == 400
    assert response.json() == {
        "type": "validation_error",
        "errors": [
            {
                "code": "captcha_required",
                "detail": "Complete the security check and try again.",
                "attr": "captcha_token",
            }
        ],
    }
    assert turnstile.posted == []
    assert Session.objects.count() == 0


def test_asking_for_a_token_does_not_count_as_a_failure(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    for n in range(5):
        attempt(api_client, email=f"person{n}@example.com")

    codes = {first_error(attempt(api_client, PASSWORD))["code"] for _ in range(15)}

    assert codes == {"captcha_required"}


def test_a_blank_token_is_the_same_as_none(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    fail_until_challenged(api_client)

    response = attempt(api_client, captcha_token="")

    assert first_error(response)["code"] == "captcha_required"


def test_a_good_token_lets_the_sign_in_go_on(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    for n in range(5):
        attempt(api_client, email=f"person{n}@example.com")

    response = attempt(api_client, PASSWORD, captcha_token="a-solved-token")

    assert response.status_code == 200
    assert len(turnstile.posted) == 1


def test_the_token_is_checked_with_the_secret_and_the_client_address(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    fail_until_challenged(api_client)

    api_client.post(
        f"{AUTH}/sign-in/",
        {
            "email": EMAIL,
            "password": "x",
            "device": device(),
            "captcha_token": "a-solved-token",
        },
        REMOTE_ADDR="198.51.100.77",
    )
    attempt(api_client, captcha_token="a-solved-token")  # the first address: challenged

    assert len(turnstile.posted) == 1
    posted = turnstile.posted[0]
    assert posted["secret"] == SECRET
    assert posted["response"] == "a-solved-token"
    assert posted["remoteip"] == "127.0.0.1"
    assert posted["idempotency_key"]


def test_the_good_token_still_gets_the_real_answer_of_the_sign_in(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    fail_until_challenged(api_client)

    wrong = attempt(api_client, captcha_token="a-solved-token")
    unknown = attempt(
        api_client, email="nobody@example.com", captcha_token="a-solved-token"
    )

    assert first_error(wrong)["code"] == "wrong_password"
    assert first_error(unknown)["code"] == "email_not_registered"


@pytest.mark.parametrize(
    "errors",
    [
        ["invalid-input-response"],
        ["timeout-or-duplicate"],
        ["missing-input-response"],
        ["bad-request"],
        [],
    ],
)
def test_a_token_that_does_not_verify_is_refused(
    api_client: APIClient, turnstile: FakeTurnstile, errors: list[str]
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    fail_until_challenged(api_client)
    turnstile.answer = {"success": False, "error-codes": errors}

    response = attempt(api_client, PASSWORD, captcha_token="a-bad-token")

    assert response.status_code == 400
    assert first_error(response) == {
        "code": "captcha_invalid",
        "detail": "The security check failed. Try again.",
        "attr": "captcha_token",
    }
    assert Session.objects.count() == 0


def test_a_token_made_for_another_flow_is_refused(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    fail_until_challenged(api_client)
    turnstile.answer = {"success": True, "action": "register"}

    response = attempt(api_client, PASSWORD, captcha_token="another-flow")

    assert first_error(response)["code"] == "captcha_invalid"


def test_a_token_without_an_action_is_accepted(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    fail_until_challenged(api_client)
    turnstile.answer = {"success": True}

    assert attempt(api_client, PASSWORD, captcha_token="t").status_code == 200


def test_bad_tokens_count_towards_the_address_lock(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    fail_until_challenged(api_client)
    turnstile.answer = {"success": False, "error-codes": ["invalid-input-response"]}

    statuses = [
        attempt(api_client, PASSWORD, captcha_token="bad").status_code
        for _ in range(11)
    ]

    assert statuses == [400] * 9 + [429, 429]


def test_a_locked_address_is_refused_before_the_service_is_asked(
    api_client: APIClient, turnstile: FakeTurnstile
) -> None:
    fail_until_challenged(api_client)
    turnstile.answer = {"success": False, "error-codes": ["invalid-input-response"]}
    for _ in range(10):
        attempt(api_client, captcha_token="bad")
    posted = len(turnstile.posted)

    response = attempt(api_client, captcha_token="t")

    assert response.status_code == 429
    assert first_error(response)["code"] == "sign_in_locked"
    assert len(turnstile.posted) == posted


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("timed out"), OSError("unreachable"), ValueError("bad json")],
)
def test_an_unreachable_service_is_a_503_not_a_free_pass(
    api_client: APIClient,
    turnstile: FakeTurnstile,
    failure: Exception,
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_client.raise_request_exception = (
        False  # a 5xx is handed back, as to a real client
    )
    VerifiedUserFactory.create(email=EMAIL)
    fail_until_challenged(api_client)
    turnstile.failure = failure

    response = attempt(api_client, PASSWORD, captcha_token="t")

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"
    assert response["Retry-After"] == "30"
    assert Session.objects.count() == 0
    assert SECRET not in caplog.text
    assert "unreachable" not in caplog.text


@pytest.mark.parametrize("errors", [["invalid-input-secret"], ["internal-error"]])
def test_a_refused_secret_or_an_outage_is_a_503(
    api_client: APIClient,
    turnstile: FakeTurnstile,
    errors: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    api_client.raise_request_exception = False
    fail_until_challenged(api_client)
    turnstile.answer = {"success": False, "error-codes": errors}

    response = attempt(api_client, PASSWORD, captcha_token="t")

    assert response.status_code == 503
    assert SECRET not in caplog.text


def test_the_service_call_is_a_form_post_to_the_cloudflare_endpoint(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.TURNSTILE_SECRET_KEY = SECRET
    seen: dict[str, Any] = {}

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"success": true}'

    def urlopen(request: Any, timeout: float) -> Response:
        seen["url"] = request.full_url
        seen["body"] = urllib.parse.parse_qs(request.data.decode())
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    assert challenge.verify("tok", "203.0.113.5") is True
    assert seen["url"] == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    assert seen["body"]["secret"] == [SECRET]
    assert seen["body"]["response"] == ["tok"]
    assert seen["body"]["remoteip"] == ["203.0.113.5"]
    assert seen["timeout"] == 3


def test_a_missing_secret_key_is_a_warning_outside_development(settings: Any) -> None:
    settings.TURNSTILE_SECRET_KEY = ""
    settings.APP_ENVIRONMENT = "staging"

    messages = user_checks.check_challenge_key(None)

    assert [m.id for m in messages] == ["users.W001"]
    assert messages[0].level == checks.WARNING


@pytest.mark.parametrize(
    ("secret", "environment"),
    [("", "development"), (SECRET, "production"), (SECRET, "staging")],
)
def test_the_key_check_is_quiet_when_it_is_fine(
    settings: Any, secret: str, environment: str
) -> None:
    settings.TURNSTILE_SECRET_KEY = secret
    settings.APP_ENVIRONMENT = environment

    assert user_checks.check_challenge_key(None) == []
