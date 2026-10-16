from typing import Any

import pytest
from django.core import mail
from rest_framework.response import Response
from rest_framework.test import APIClient

from conftest import Clock
from users.models import EmailCode, User
from users.tests.factories import UserFactory, VerifiedUserFactory
from users.tests.helpers import AUTH, device, emailed_code, first_error
from users.throttles import ResendCodeThrottle

pytestmark = pytest.mark.django_db

EMAIL = "sara@example.com"
ANSWER = {"code_expires_in": 600, "resend_available_in": 60}


def resend(client: APIClient, email: str = EMAIL) -> Response:
    return client.post(f"{AUTH}/resend-code/", {"email": email})


def verify(client: APIClient, code: str) -> Response:
    body: dict[str, Any] = {"email": EMAIL, "code": code, "device": device()}
    return client.post(f"{AUTH}/verify-email/", body)


def test_a_new_code_is_emailed_and_replaces_the_old_one(
    api_client: APIClient, clock: Clock
) -> None:
    UserFactory.create(email=EMAIL)
    assert resend(api_client).json() == ANSWER
    first = emailed_code()
    clock.advance(seconds=61)

    response = resend(api_client)

    assert response.status_code == 200
    assert response.json() == ANSWER
    assert len(mail.outbox) == 2
    assert EmailCode.objects.count() == 1
    assert EmailCode.objects.get().attempts == 0
    second = emailed_code()
    if first != second:
        assert verify(api_client, first).status_code == 400
    assert verify(api_client, second).status_code == 200


def test_the_answer_is_the_same_for_an_unknown_address(api_client: APIClient) -> None:
    response = resend(api_client, "nobody@example.com")

    assert response.status_code == 200
    assert response.json() == ANSWER
    assert mail.outbox == []
    assert User.objects.count() == 0


def test_a_verified_address_gets_the_same_answer_and_no_email(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    response = resend(api_client)

    assert response.json() == ANSWER
    assert mail.outbox == []


def test_an_unknown_address_is_limited_exactly_like_a_known_one(
    api_client: APIClient,
) -> None:
    UserFactory.create(email=EMAIL)

    for address in (EMAIL, "nobody@example.com"):
        assert resend(api_client, address).status_code == 200
        second = resend(api_client, address)
        assert second.status_code == 429
        assert first_error(second)["code"] == "resend_too_soon"
        assert second["Retry-After"] == "60"


def test_the_cooldown_counts_down(api_client: APIClient, clock: Clock) -> None:
    UserFactory.create(email=EMAIL)
    resend(api_client)
    clock.advance(seconds=45)

    response = resend(api_client)

    assert response.status_code == 429
    assert response["Retry-After"] == "15"
    assert len(mail.outbox) == 1


def test_the_hourly_limit_has_its_own_code_and_a_long_wait(
    api_client: APIClient, clock: Clock
) -> None:
    UserFactory.create(email=EMAIL)
    for _ in range(5):
        assert resend(api_client).status_code == 200
        clock.advance(seconds=61)

    response = resend(api_client)

    assert response.status_code == 429
    assert first_error(response)["code"] == "resend_limit_reached"
    assert int(response["Retry-After"]) == 3600 - 5 * 61
    assert len(mail.outbox) == 5


def test_a_registration_and_a_resend_share_the_hourly_budget(
    api_client: APIClient, clock: Clock
) -> None:
    api_client.post(
        f"{AUTH}/register/",
        {"name": "Sara", "email": EMAIL, "password": "a-fine-passphrase-7"},
    )
    clock.advance(seconds=61)
    for _ in range(4):
        assert resend(api_client).status_code == 200
        clock.advance(seconds=61)

    assert first_error(resend(api_client))["code"] == "resend_limit_reached"


def test_the_email_must_be_valid(api_client: APIClient) -> None:
    response = resend(api_client, "nope")

    assert response.status_code == 400
    assert first_error(response)["attr"] == "email"


def test_a_client_address_is_throttled(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ResendCodeThrottle, "THROTTLE_RATES", {"resend_code": "2/hour"})

    statuses = [
        resend(api_client, f"person{n}@example.com").status_code for n in range(3)
    ]

    assert statuses == [200, 200, 429]
