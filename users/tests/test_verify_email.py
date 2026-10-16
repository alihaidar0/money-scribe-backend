from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.test import APIClient

from conftest import Clock
from users import tokens
from users.models import EmailCode, Session, User
from users.tests.factories import UserFactory, VerifiedUserFactory
from users.tests.helpers import AUTH, device, emailed_code, first_error
from users.throttles import VerifyEmailThrottle

pytestmark = pytest.mark.django_db

EMAIL = "sara@example.com"


def start(client: APIClient, **overrides: Any) -> str:
    """Register ``EMAIL`` and return the code that was emailed."""
    body = {
        "name": "Sara Example",
        "email": EMAIL,
        "password": "a-fine-passphrase-7",
        **overrides,
    }
    assert client.post(f"{AUTH}/register/", body).status_code == 201
    return emailed_code()


def verify(client: APIClient, code: str, **overrides: Any) -> Response:
    return client.post(
        f"{AUTH}/verify-email/",
        {"email": EMAIL, "code": code, "device": device(), **overrides},
    )


def wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def test_the_right_code_verifies_the_email_and_signs_the_user_in(
    api_client: APIClient,
) -> None:
    code = start(api_client)

    response = verify(api_client, code)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_in",
        "user",
    }
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    user = User.objects.get()
    assert body["user"] == {"id": str(user.id), "name": "Sara Example", "email": EMAIL}
    assert user.email_verified_at is not None
    assert user.last_login is not None
    assert response["Cache-Control"] == "no-store"
    claims = tokens.decode_access_token(body["access_token"])
    assert claims.user_id == user.id


def test_the_call_creates_the_session_with_the_device(api_client: APIClient) -> None:
    code = start(api_client)

    api_client.post(
        f"{AUTH}/verify-email/",
        {"email": EMAIL, "code": code, "device": device()},
        REMOTE_ADDR="203.0.113.77",
    )

    session = Session.objects.get()
    assert session.user.email == EMAIL
    assert (
        session.device_name,
        session.platform,
        session.os_version,
        session.app_version,
    ) == (
        "Pixel 8",
        "android",
        "Android 15",
        "1.0.0+1",
    )
    assert str(session.device_id) == "0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d"
    assert session.ip == "203.0.113.0"
    assert session.user_agent == ""
    assert session.ended_at is None
    assert (session.expires_at - session.created_at).days == 90
    assert session.generation == 0
    assert session.refresh_hash == tokens.hash_token(
        tokens.issue_refresh_token(session)
    )


def test_the_web_keeps_the_user_agent_and_the_apps_do_not(
    api_client: APIClient,
) -> None:
    code = start(api_client)

    api_client.post(
        f"{AUTH}/verify-email/",
        {
            "email": EMAIL,
            "code": code,
            "device": device(platform="web", device_name="Chrome on Windows"),
        },
        HTTP_USER_AGENT="Mozilla/5.0 (X11) Test",
    )

    assert Session.objects.get().user_agent == "Mozilla/5.0 (X11) Test"


def test_the_verifying_device_sets_the_language_and_time_zone(
    api_client: APIClient,
) -> None:
    code = start(api_client, language="en", time_zone="UTC")

    verify(api_client, code, language="ar", time_zone="Asia/Riyadh")

    user = User.objects.get()
    assert (user.language, user.time_zone) == ("ar", "Asia/Riyadh")


def test_a_used_code_cannot_be_used_twice(api_client: APIClient) -> None:
    code = start(api_client)
    verify(api_client, code)

    response = verify(api_client, code)

    assert response.status_code == 400
    assert first_error(response)["code"] == "code_invalid"
    assert Session.objects.count() == 1
    assert EmailCode.objects.count() == 0


def test_a_wrong_code_reports_the_attempts_left(api_client: APIClient) -> None:
    code = start(api_client)

    response = verify(api_client, wrong(code))

    assert response.status_code == 400
    assert response.json()["type"] == "validation_error"
    error = first_error(response)
    assert error["code"] == "code_invalid"
    assert error["attr"] == "code"
    assert error["attempts_left"] == 4
    assert Session.objects.count() == 0


def test_the_attempts_run_down_and_survive_the_error(api_client: APIClient) -> None:
    code = start(api_client)

    left = [
        first_error(verify(api_client, wrong(code)))["attempts_left"] for _ in range(5)
    ]

    assert left == [4, 3, 2, 1, 0]
    assert EmailCode.objects.get().attempts == 5


def test_the_right_code_still_works_on_the_last_attempt(api_client: APIClient) -> None:
    code = start(api_client)
    for _ in range(4):
        verify(api_client, wrong(code))

    assert verify(api_client, code).status_code == 200


def test_after_the_last_attempt_even_the_right_code_is_refused(
    api_client: APIClient, clock: Clock
) -> None:
    code = start(api_client)
    for _ in range(5):
        verify(api_client, wrong(code))
    clock.advance(seconds=20)

    response = verify(api_client, code)

    assert response.status_code == 429
    assert first_error(response)["code"] == "code_attempts_exceeded"
    assert response["Retry-After"] == "40"
    assert Session.objects.count() == 0


def test_a_new_code_revives_the_flow_after_the_attempts_ran_out(
    api_client: APIClient, clock: Clock
) -> None:
    code = start(api_client)
    for _ in range(5):
        verify(api_client, wrong(code))
    clock.advance(seconds=61)

    assert api_client.post(f"{AUTH}/resend-code/", {"email": EMAIL}).status_code == 200

    assert verify(api_client, emailed_code()).status_code == 200


def test_an_expired_code_says_so_and_costs_no_attempt(
    api_client: APIClient, clock: Clock
) -> None:
    code = start(api_client)
    clock.advance(seconds=601)

    response = verify(api_client, code)

    assert response.status_code == 400
    assert first_error(response)["code"] == "code_expired"
    assert first_error(response)["attr"] == "code"
    assert EmailCode.objects.get().attempts == 0


def test_the_code_is_valid_until_its_last_second(
    api_client: APIClient, clock: Clock
) -> None:
    code = start(api_client)
    clock.advance(seconds=599)

    assert verify(api_client, code).status_code == 200


def resend_for(client: APIClient, email: str) -> None:
    client.post(f"{AUTH}/resend-code/", {"email": email})


def test_an_unknown_address_is_answered_like_a_real_one(api_client: APIClient) -> None:
    start(api_client)
    real = first_error(verify(api_client, wrong(emailed_code())))
    resend_for(api_client, "nobody@example.com")

    unknown = first_error(
        api_client.post(
            f"{AUTH}/verify-email/",
            {"email": "nobody@example.com", "code": "000000", "device": device()},
        )
    )

    assert unknown == real
    assert unknown["attempts_left"] == 4


def test_an_unknown_address_runs_out_of_attempts_too(api_client: APIClient) -> None:
    resend_for(api_client, "nobody@example.com")
    body = {"email": "nobody@example.com", "code": "000000", "device": device()}

    statuses = [
        api_client.post(f"{AUTH}/verify-email/", body).status_code for _ in range(7)
    ]
    last = api_client.post(f"{AUTH}/verify-email/", body)

    assert statuses == [400, 400, 400, 400, 400, 429, 429]
    assert first_error(last)["code"] == "code_attempts_exceeded"
    assert int(last["Retry-After"]) >= 1


def test_an_address_nothing_was_sent_to_has_no_live_code(api_client: APIClient) -> None:
    body = {"email": "nobody@example.com", "code": "000000", "device": device()}

    response = api_client.post(f"{AUTH}/verify-email/", body)

    assert response.status_code == 400
    assert first_error(response)["code"] == "code_expired"


def sequence(
    client: APIClient, clock: Clock, email: str, wrong_code: str
) -> list[tuple[int, str, object, str | None]]:
    """What verifying says over a whole story: wrong tries, a resend, a wrong try, then ten minutes."""
    story: list[tuple[int, str, object, str | None]] = []

    def step() -> None:
        response = client.post(
            f"{AUTH}/verify-email/",
            {"email": email, "code": wrong_code, "device": device()},
        )
        error = first_error(response)
        story.append(
            (
                response.status_code,
                error["code"],
                error.get("attempts_left"),
                response.get("Retry-After"),
            )
        )

    for _ in range(6):
        step()
    clock.advance(seconds=61)
    resend_for(client, email)
    step()
    clock.advance(seconds=601)
    step()
    return story


@pytest.mark.parametrize("kind", ["unknown", "verified"])
def test_a_pending_address_cannot_be_told_from_any_other_by_verifying(
    api_client: APIClient, clock: Clock, kind: str
) -> None:
    """Exactly the same answers, step for step: status, code, attempts_left and Retry-After."""
    start(api_client)
    real = sequence(api_client, clock, EMAIL, wrong(emailed_code()))
    cache.clear()
    clock.current -= timedelta(seconds=61 + 601)
    other = "nobody@example.com"
    if kind == "verified":
        other = "owner@example.com"
        VerifiedUserFactory.create(email=other)
    # The same first step as a registration: one code goes out to the address (or its owner's notice).
    api_client.post(
        f"{AUTH}/register/",
        {"name": "X", "email": other, "password": "a-fine-passphrase-7"},
    )

    assert sequence(api_client, clock, other, "000000") == real
    assert real[5][1] == "code_attempts_exceeded"
    assert real[-1][1] == "code_expired"


def test_a_verified_address_has_nothing_to_verify(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    resend_for(api_client, EMAIL)

    response = verify(api_client, "123456")

    assert response.status_code == 400
    assert first_error(response)["code"] == "code_invalid"
    assert first_error(response)["attempts_left"] == 4
    assert Session.objects.count() == 0


def test_a_code_belongs_to_its_user(api_client: APIClient) -> None:
    other = UserFactory.create(email="other@example.com")
    code = start(api_client)
    api_client.post(
        f"{AUTH}/register/",
        {"name": "Other", "email": other.email, "password": "a-fine-passphrase-7"},
    )
    other_code = emailed_code()

    # Each code verifies only its own address (the same digits would still hash differently).
    assert mail.outbox[0].to == [EMAIL]
    assert mail.outbox[1].to == [other.email]
    assert verify(api_client, code).status_code == 200
    other_response = api_client.post(
        f"{AUTH}/verify-email/",
        {"email": other.email, "code": other_code, "device": device()},
    )
    assert other_response.status_code == 200
    assert Session.objects.count() == 2


@pytest.mark.parametrize(
    "code",
    [
        "12a456",
        "12345",
        "1234567",
        "",
        "\uff11\uff12\uff13\uff14\uff15\uff16",
        " 12345",
    ],
)
def test_a_malformed_code_is_a_plain_field_error(
    api_client: APIClient, code: str
) -> None:
    start(api_client)

    response = verify(api_client, code)

    assert response.status_code == 400
    assert first_error(response)["attr"] == "code"
    assert first_error(response)["code"] in {"invalid", "blank"}
    assert EmailCode.objects.get().attempts == 0


@pytest.mark.parametrize(
    ("body", "attr"),
    [
        ({"platform": "windows"}, "device.platform"),
        ({"device_id": "not-a-uuid"}, "device.device_id"),
        ({"device_name": "x" * 101}, "device.device_name"),
        ({"app_version": ""}, "device.app_version"),
    ],
)
def test_the_device_is_validated(
    api_client: APIClient, body: dict[str, str], attr: str
) -> None:
    code = start(api_client)

    response = verify(api_client, code, device=device(**body))

    assert response.status_code == 400
    assert first_error(response)["attr"] == attr


def test_the_device_is_required(api_client: APIClient) -> None:
    code = start(api_client)

    response = api_client.post(f"{AUTH}/verify-email/", {"email": EMAIL, "code": code})

    assert response.status_code == 400
    assert first_error(response) == {
        "code": "required",
        "detail": "This field is required.",
        "attr": "device",
    }


def test_the_os_version_may_be_left_out(api_client: APIClient) -> None:
    code = start(api_client)
    without_os = {key: value for key, value in device().items() if key != "os_version"}

    assert verify(api_client, code, device=without_os).status_code == 200
    assert Session.objects.get().os_version == ""


def test_a_client_address_is_throttled(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        VerifyEmailThrottle, "THROTTLE_RATES", {"verify_email": "2/hour"}
    )
    code = start(api_client)

    statuses = [verify(api_client, wrong(code)).status_code for _ in range(3)]

    assert statuses == [400, 400, 429]


def test_an_inactive_account_cannot_verify(api_client: APIClient) -> None:
    code = start(api_client)
    User.objects.update(is_active=False)

    assert verify(api_client, code).status_code == 400
    assert Session.objects.count() == 0
