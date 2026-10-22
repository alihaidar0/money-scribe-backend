"""Password reset in three steps (ADR 0014): request a code, verify it for a token, set the password."""

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient

from conftest import Clock
from users.models import (
    EmailCode,
    EmailCodePurpose,
    EndReason,
    PasswordResetToken,
    Session,
    User,
)
from users.tests.factories import UserFactory, VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    bearer,
    emailed_code,
    first_error,
    refresh,
    sign_in,
)

pytestmark = pytest.mark.django_db

EMAIL = "sara@example.com"
NEW_PASSWORD = "a-fine-passphrase-7"
ANSWER = {"code_expires_in": 600, "resend_available_in": 60}


def request_reset(client: APIClient, email: str = EMAIL) -> Response:
    return client.post(f"{AUTH}/password-reset/request/", {"email": email})


def verify_reset(client: APIClient, code: str, email: str = EMAIL) -> Response:
    return client.post(f"{AUTH}/password-reset/verify/", {"email": email, "code": code})


def confirm_reset(
    client: APIClient, reset_token: str, password: str = NEW_PASSWORD
) -> Response:
    return client.post(
        f"{AUTH}/password-reset/confirm/",
        {"reset_token": reset_token, "password": password},
    )


def get_reset_token(client: APIClient, email: str = EMAIL) -> str:
    """Run the first two steps for a verified user and return the reset token."""
    assert request_reset(client, email).status_code == 200
    response = verify_reset(client, emailed_code(), email)
    assert response.status_code == 200, response.json()
    token: str = response.json()["reset_token"]
    return token


# --- step 1: request ---------------------------------------------------------------------------


def test_a_code_is_emailed_to_a_verified_account(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL, language="en")

    response = request_reset(api_client)

    assert response.status_code == 200
    assert response.json() == ANSWER
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [EMAIL]
    assert "password reset" in mail.outbox[0].subject
    record = EmailCode.objects.get()
    assert record.purpose == EmailCodePurpose.PASSWORD_RESET
    assert emailed_code() not in record.code_hash


def test_the_email_is_in_the_language_of_the_account(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL, language="ar")

    request_reset(api_client)

    assert "إعادة تعيين كلمة المرور" in mail.outbox[0].subject


def test_the_email_address_is_normalized(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    assert request_reset(api_client, "  Sara@Example.COM ").status_code == 200

    assert len(mail.outbox) == 1


@pytest.mark.parametrize("state", ["unknown", "pending", "inactive"])
def test_the_answer_is_the_same_for_every_address_and_nothing_is_sent(
    api_client: APIClient, state: str
) -> None:
    if state == "pending":
        UserFactory.create(email=EMAIL)
    if state == "inactive":
        VerifiedUserFactory.create(email=EMAIL, is_active=False)

    response = request_reset(api_client)

    assert response.status_code == 200
    assert response.json() == ANSWER
    assert mail.outbox == []
    assert EmailCode.objects.count() == 0


def test_a_new_request_replaces_the_code_and_the_token(
    api_client: APIClient, clock: Clock
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    get_reset_token(api_client)
    clock.advance(seconds=61)

    request_reset(api_client)

    assert EmailCode.objects.count() == 1
    assert PasswordResetToken.objects.count() == 0


def test_the_cooldown_and_the_hourly_budget_answer_429_with_retry_after(
    api_client: APIClient, clock: Clock
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    request_reset(api_client)

    soon = request_reset(api_client)

    assert soon.status_code == 429
    assert first_error(soon)["code"] == "resend_too_soon"
    assert soon["Retry-After"] == "60"
    for _ in range(4):
        clock.advance(seconds=61)
        assert request_reset(api_client).status_code == 200
    clock.advance(seconds=61)
    limit = request_reset(api_client)
    assert first_error(limit)["code"] == "resend_limit_reached"
    assert int(limit["Retry-After"]) > 60


def test_the_429_is_the_same_for_an_unknown_address(api_client: APIClient) -> None:
    request_reset(api_client, "nobody@example.com")

    soon = request_reset(api_client, "nobody@example.com")

    assert first_error(soon)["code"] == "resend_too_soon"


@pytest.mark.parametrize("body", [{}, {"email": "not-an-email"}])
def test_a_bad_request_is_a_400(api_client: APIClient, body: dict[str, Any]) -> None:
    response = api_client.post(f"{AUTH}/password-reset/request/", body)

    assert response.status_code == 400
    assert first_error(response)["attr"] == "email"


# --- step 2: verify ----------------------------------------------------------------------------


def test_a_right_code_is_spent_for_a_token_and_signs_nobody_in(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    request_reset(api_client)

    response = verify_reset(api_client, emailed_code())

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {"reset_token", "expires_in"}
    assert body["expires_in"] == 600
    assert Session.objects.count() == 0
    assert EmailCode.objects.count() == 0
    stored = PasswordResetToken.objects.get()
    assert body["reset_token"] not in stored.token_hash
    assert len(stored.token_hash) == 64


def test_a_code_works_once(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    request_reset(api_client)
    code = emailed_code()
    verify_reset(api_client, code)

    again = verify_reset(api_client, code)

    assert again.status_code == 400
    assert first_error(again)["code"] == "code_invalid"
    assert first_error(again)["attempts_left"] == 4


def test_a_wrong_code_counts_down_and_the_sixth_try_is_refused(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    request_reset(api_client)
    right = emailed_code()
    wrong = "000000" if right != "000000" else "111111"

    for left in (4, 3, 2, 1, 0):
        response = verify_reset(api_client, wrong)
        assert response.status_code == 400
        assert first_error(response)["code"] == "code_invalid"
        assert first_error(response)["attempts_left"] == left
    dead = verify_reset(api_client, wrong)

    assert dead.status_code == 429
    assert first_error(dead)["code"] == "code_attempts_exceeded"
    assert int(dead["Retry-After"]) >= 1
    assert verify_reset(api_client, right).status_code == 429


def test_an_expired_code_is_refused(api_client: APIClient, clock: Clock) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    request_reset(api_client)
    code = emailed_code()
    clock.advance(minutes=11)

    response = verify_reset(api_client, code)

    assert first_error(response)["code"] == "code_expired"


def test_an_address_with_no_reset_is_answered_like_a_real_one(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    request_reset(api_client)
    request_reset(api_client, "nobody@example.com")

    real = verify_reset(api_client, "000000")
    unknown = verify_reset(api_client, "000000", "nobody@example.com")

    assert real.status_code == unknown.status_code == 400
    assert first_error(real) == first_error(unknown)
    assert first_error(unknown)["attempts_left"] == 4


def test_nothing_asked_for_is_expired_for_everyone(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    real = verify_reset(api_client, "123456")
    unknown = verify_reset(api_client, "123456", "nobody@example.com")

    assert first_error(real)["code"] == first_error(unknown)["code"] == "code_expired"


def test_a_registration_attempt_cannot_tell_a_real_account_from_an_unknown_address(
    api_client: APIClient,
) -> None:
    """Anyone can make the server note a send for any address by trying to register it, so the
    answer of the reset verification must not depend on whether that address has an account."""
    VerifiedUserFactory.create(email=EMAIL)
    registration = {"name": "Sara", "password": "a-fine-passphrase-7"}
    for email in (EMAIL, "nobody@example.com"):
        api_client.post(f"{AUTH}/resend-code/", {"email": email})
        api_client.post(f"{AUTH}/register/", {"email": email, **registration})

    answers = [
        [verify_reset(api_client, "000000", email) for _ in range(6)]
        for email in (EMAIL, "nobody@example.com")
    ]

    story = [[(r.status_code, first_error(r)) for r in run] for run in answers]
    assert story[0] == story[1]


def test_a_reset_code_is_no_use_for_verifying_an_email(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    request_reset(api_client)

    response = api_client.post(
        f"{AUTH}/verify-email/",
        {
            "email": EMAIL,
            "code": emailed_code(),
            "device": {
                "device_id": "0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d",
                "device_name": "Pixel 8",
                "platform": "android",
                "app_version": "1.0.0+1",
            },
        },
    )

    assert response.status_code == 400
    assert Session.objects.count() == 0


@pytest.mark.parametrize("code", ["12345", "1234567", "12345a", ""])
def test_a_malformed_code_is_a_400(api_client: APIClient, code: str) -> None:
    response = verify_reset(api_client, code)

    assert response.status_code == 400
    assert first_error(response)["attr"] == "code"


# --- step 3: confirm ---------------------------------------------------------------------------


def test_the_new_password_replaces_the_old_one(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)

    response = confirm_reset(api_client, token)

    assert response.status_code == 204
    assert response.content == b""
    assert sign_in(api_client, EMAIL, NEW_PASSWORD).status_code == 200
    assert sign_in(api_client, EMAIL).status_code == 401


def test_every_session_ends_and_says_why(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    first = sign_in(api_client, EMAIL).json()
    second = sign_in(api_client, EMAIL).json()
    token = get_reset_token(api_client)

    confirm_reset(api_client, token)

    assert Session.objects.filter(ended_at__isnull=True).count() == 0
    assert set(Session.objects.values_list("end_reason", flat=True)) == {
        EndReason.PASSWORD_CHANGED
    }
    for body in (first, second):
        profile = api_client.get(
            f"{AUTH}/profile/", headers=bearer(body["access_token"])
        )
        assert first_error(profile)["code"] == "session_password_changed"
        renewed = refresh(api_client, body["refresh_token"])
        assert first_error(renewed)["code"] == "session_password_changed"


def test_the_owner_is_told_about_the_change(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)

    confirm_reset(api_client, token)

    assert len(mail.outbox) == 2
    assert mail.outbox[-1].to == [EMAIL]
    assert "password was changed" in mail.outbox[-1].subject


def test_a_token_works_once(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)
    confirm_reset(api_client, token)

    again = confirm_reset(api_client, token, "another-fine-passphrase-9")

    assert again.status_code == 400
    assert first_error(again)["code"] == "reset_token_invalid"
    assert first_error(again)["attr"] == "reset_token"
    assert PasswordResetToken.objects.count() == 0
    assert sign_in(api_client, EMAIL, NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize("token", ["not-a-token", "x" * 43])
def test_an_unknown_token_is_refused(api_client: APIClient, token: str) -> None:
    VerifiedUserFactory.create(email=EMAIL)

    response = confirm_reset(api_client, token)

    assert response.status_code == 400
    assert first_error(response)["code"] == "reset_token_invalid"


def test_an_expired_token_is_refused(api_client: APIClient, clock: Clock) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)
    clock.advance(minutes=11)

    response = confirm_reset(api_client, token)

    assert first_error(response)["code"] == "reset_token_invalid"
    assert sign_in(api_client, EMAIL, NEW_PASSWORD).status_code == 401


def test_a_deactivated_account_cannot_be_reset(api_client: APIClient) -> None:
    user = VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)
    User.objects.filter(pk=user.pk).update(is_active=False)

    response = confirm_reset(api_client, token)

    assert first_error(response)["code"] == "reset_token_invalid"


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("short", "password_too_short"),
        ("x" * 129, "password_too_long"),
        ("1q2w3e4r5t6y7u8i9o0p", "password_too_common"),
        ("sara@example.com!!!!", "password_too_similar"),
    ],
)
def test_the_password_rules_apply_and_keep_the_token(
    api_client: APIClient, password: str, code: str
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)

    response = confirm_reset(api_client, token, password)

    assert response.status_code == 400
    assert code in {item["code"] for item in response.json()["errors"]}
    assert {item["attr"] for item in response.json()["errors"]} == {"password"}
    assert PasswordResetToken.objects.count() == 1
    assert confirm_reset(api_client, token).status_code == 204


def test_a_compromised_password_is_refused(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.PWNED_PASSWORD_CHECK = True
    monkeypatch.setattr("users.validators.breach_count", lambda password: 12345)
    VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)

    response = confirm_reset(api_client, token)

    assert first_error(response)["code"] == "password_compromised"


def test_the_old_password_may_be_chosen_again(api_client: APIClient) -> None:
    """A repeat is not refused: that would let whoever reads the mailbox test guesses of the password."""
    VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)

    response = confirm_reset(api_client, token, "valid-pass-1-and-longer")

    assert response.status_code == 204


def test_a_blank_or_missing_field_is_a_400(api_client: APIClient) -> None:
    response = api_client.post(f"{AUTH}/password-reset/confirm/", {"password": ""})

    assert response.status_code == 400
    assert {item["attr"] for item in response.json()["errors"]} == {
        "reset_token",
        "password",
    }


def test_a_new_password_clears_the_pending_codes(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    token = get_reset_token(api_client)
    EmailCode.objects.create(
        user=User.objects.get(email=EMAIL),
        purpose=EmailCodePurpose.PASSWORD_RESET,
        code_hash="h",
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    confirm_reset(api_client, token)

    assert EmailCode.objects.count() == 0


def test_a_token_changes_only_its_own_account(api_client: APIClient) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    other = VerifiedUserFactory.create(email="omar@example.com")
    other_session = sign_in(api_client, other.email).json()
    token = get_reset_token(api_client)

    confirm_reset(api_client, token)

    assert sign_in(api_client, other.email).status_code == 200
    profile = api_client.get(
        f"{AUTH}/profile/", headers=bearer(other_session["access_token"])
    )
    assert profile.status_code == 200


def test_a_token_of_one_account_is_no_use_after_a_second_reset_of_another(
    api_client: APIClient,
) -> None:
    VerifiedUserFactory.create(email=EMAIL)
    VerifiedUserFactory.create(email="omar@example.com")
    sara_token = get_reset_token(api_client)
    omar_token = get_reset_token(api_client, "omar@example.com")

    assert confirm_reset(api_client, omar_token).status_code == 204
    assert confirm_reset(api_client, sara_token).status_code == 204
    assert sign_in(api_client, EMAIL, NEW_PASSWORD).status_code == 200
    assert sign_in(api_client, "omar@example.com", NEW_PASSWORD).status_code == 200
