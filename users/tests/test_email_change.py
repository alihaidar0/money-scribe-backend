"""Changing the sign-in address (ADR 0015): a code to the new address, then the switch."""

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient

from conftest import Clock
from users import limits
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
    PASSWORD,
    bearer,
    emailed_code,
    first_error,
    new_device,
    refresh,
    sign_in,
    start_session,
)
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db

CHANGE = f"{AUTH}/email-change/"
CONFIRM = f"{AUTH}/email-change/confirm/"
PROFILE = f"{AUTH}/profile/"
NEW = "sara.new@example.com"
ANSWER = {"email": NEW, "code_expires_in": 600, "resend_available_in": 60}


def request_change(
    client: APIClient,
    access_token: str,
    email: str = NEW,
    current_password: str = PASSWORD,
) -> Response:
    return client.post(
        CHANGE,
        {"email": email, "current_password": current_password},
        headers=bearer(access_token),
    )


def confirm(client: APIClient, access_token: str, code: str) -> Response:
    return client.post(CONFIRM, {"code": code}, headers=bearer(access_token))


def pending_code() -> str:
    return emailed_code()


# --- step 1: request ---------------------------------------------------------------------------


def test_the_code_goes_to_the_new_address_and_nothing_changes_yet(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client, email="sara@example.com", language="en")
    old = user.email

    response = request_change(api_client, body["access_token"])

    assert response.status_code == 200
    assert response.json() == ANSWER
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [NEW]
    assert old not in str(mail.outbox[0].body)
    record = EmailCode.objects.get()
    assert record.purpose == EmailCodePurpose.EMAIL_CHANGE
    assert record.payload == NEW
    assert pending_code() not in record.code_hash
    user.refresh_from_db()
    assert user.email == old
    assert sign_in(api_client, old).status_code == 200


def test_the_email_is_in_the_language_of_the_account(api_client: APIClient) -> None:
    _, body = start_session(api_client, language="ar")

    request_change(api_client, body["access_token"])

    assert "أكّد" in mail.outbox[0].subject


def test_the_address_is_normalized(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = request_change(
        api_client, body["access_token"], "  Sara.New@Example.COM "
    )

    assert response.json()["email"] == NEW
    assert EmailCode.objects.get().payload == NEW


def test_a_wrong_password_is_a_400_and_sends_nothing(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = request_change(api_client, body["access_token"], current_password="nope")

    assert response.status_code == 400
    error = first_error(response)
    assert error["code"] == "invalid_password"
    assert error["attr"] == "current_password"
    assert mail.outbox == []
    assert EmailCode.objects.count() == 0


def test_the_password_lock_is_shared_with_the_password_change(
    api_client: APIClient,
) -> None:
    _, body = start_session(api_client)
    for _ in range(5):
        request_change(api_client, body["access_token"], current_password="nope")

    response = request_change(api_client, body["access_token"])

    assert response.status_code == 429
    assert first_error(response)["code"] == "password_attempts_exceeded"
    assert mail.outbox == []


@pytest.mark.parametrize("same", ["sara@example.com", " SARA@example.com "])
def test_the_address_the_account_already_has_is_refused(
    api_client: APIClient, same: str
) -> None:
    _, body = start_session(api_client, email="sara@example.com")

    response = request_change(api_client, body["access_token"], same)

    assert response.status_code == 400
    error = first_error(response)
    assert error["code"] == "email_unchanged"
    assert error["attr"] == "email"


@pytest.mark.parametrize("state", ["verified", "pending"])
def test_a_taken_address_is_a_409_for_a_signed_in_caller(
    api_client: APIClient, state: str
) -> None:
    factory = VerifiedUserFactory if state == "verified" else UserFactory
    factory.create(email=NEW)
    _, body = start_session(api_client)

    response = request_change(api_client, body["access_token"])

    assert response.status_code == 409
    error = first_error(response)
    assert error["code"] == "email_already_registered"
    assert error["attr"] is None
    assert mail.outbox == []
    assert EmailCode.objects.count() == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": NEW},
        {"current_password": PASSWORD},
        {"email": "nope", "current_password": PASSWORD},
    ],
)
def test_a_bad_request_is_a_400(api_client: APIClient, body: dict[str, str]) -> None:
    _, tokens = start_session(api_client)

    response = api_client.post(CHANGE, body, headers=bearer(tokens["access_token"]))

    assert response.status_code == 400
    assert mail.outbox == []


def test_a_new_request_replaces_the_pending_change(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    request_change(api_client, body["access_token"], "first@example.com")
    first_code = pending_code()
    clock.advance(seconds=61)
    token = refresh(api_client, body["refresh_token"]).json()["access_token"]

    request_change(api_client, token, "second@example.com")

    record = EmailCode.objects.get()
    assert record.payload == "second@example.com"
    if first_code != pending_code():
        assert confirm(api_client, token, first_code).status_code == 400


def test_the_same_new_address_has_a_cooldown(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    request_change(api_client, body["access_token"])

    again = request_change(api_client, body["access_token"])

    assert again.status_code == 429
    assert first_error(again)["code"] == "resend_too_soon"
    assert again["Retry-After"] == "60"


def test_one_account_may_start_only_five_changes_an_hour(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    for number in range(5):
        response = request_change(
            api_client, body["access_token"], f"n{number}@example.com"
        )
        assert response.status_code == 200

    sixth = request_change(api_client, body["access_token"], "n6@example.com")

    assert sixth.status_code == 429
    assert first_error(sixth)["code"] == "resend_limit_reached"
    assert int(sixth["Retry-After"]) > 60
    assert len(mail.outbox) == 5


def test_refused_attempts_count_toward_the_cap(api_client: APIClient) -> None:
    """Probing addresses with 409 is bounded by the same cap."""
    VerifiedUserFactory.create(email=NEW)
    _, body = start_session(api_client)
    for _ in range(5):
        assert request_change(api_client, body["access_token"]).status_code == 409

    assert (
        request_change(api_client, body["access_token"], "free@example.com").status_code
        == 429
    )


def test_the_cap_is_per_account(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    _, other = start_session(api_client)
    for number in range(6):
        request_change(api_client, body["access_token"], f"n{number}@example.com")

    assert (
        request_change(
            api_client, other["access_token"], "free@example.com"
        ).status_code
        == 200
    )


# --- step 2: confirm ---------------------------------------------------------------------------


def start_change(client: APIClient, tokens: dict[str, Any]) -> str:
    assert request_change(client, tokens["access_token"]).status_code == 200
    return pending_code()


def test_the_right_code_switches_the_address(api_client: APIClient) -> None:
    user, body = start_session(api_client, email="sara@example.com")
    code = start_change(api_client, body)

    response = confirm(api_client, body["access_token"], code)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.pk),
        "name": user.name,
        "email": NEW,
        "language": "en",
        "time_zone": "UTC",
    }
    user.refresh_from_db()
    assert user.email == NEW
    assert user.email_verified_at is not None
    assert EmailCode.objects.count() == 0
    assert sign_in(api_client, NEW).status_code == 200
    gone = sign_in(api_client, "sara@example.com")
    assert first_error(gone)["code"] == "email_not_registered"


def test_the_old_address_is_told_without_the_new_one(api_client: APIClient) -> None:
    _, body = start_session(api_client, email="sara@example.com", language="en")
    code = start_change(api_client, body)

    confirm(api_client, body["access_token"], code)

    assert len(mail.outbox) == 2
    notice = mail.outbox[-1]
    assert notice.to == ["sara@example.com"]
    assert "email" in notice.subject
    assert NEW not in str(notice.body)


def test_the_other_sessions_end_and_this_one_continues(api_client: APIClient) -> None:
    user, first = start_session(api_client)
    second = sign_in(api_client, user.email, device=new_device()).json()
    code = start_change(api_client, first)

    confirm(api_client, first["access_token"], code)

    ended = Session.objects.filter(ended_at__isnull=False)
    assert ended.count() == 1
    assert ended.get().end_reason == EndReason.EMAIL_CHANGED
    profile = api_client.get(PROFILE, headers=bearer(second["access_token"]))
    assert first_error(profile)["code"] == "session_email_changed"
    assert (
        first_error(refresh(api_client, second["refresh_token"]))["code"]
        == "session_email_changed"
    )
    same = api_client.get(PROFILE, headers=bearer(first["access_token"]))
    assert same.status_code == 200
    assert same.json()["email"] == NEW
    assert refresh(api_client, first["refresh_token"]).status_code == 200


def test_password_reset_codes_for_the_old_address_die_with_the_change(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    EmailCode.objects.create(
        user=user,
        purpose=EmailCodePurpose.PASSWORD_RESET,
        code_hash="h",
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    PasswordResetToken.objects.create(
        user=user, token_hash="t" * 64, expires_at=timezone.now() + timedelta(minutes=5)
    )
    code = start_change(api_client, body)

    confirm(api_client, body["access_token"], code)

    assert PasswordResetToken.objects.count() == 0
    assert (
        EmailCode.objects.filter(purpose=EmailCodePurpose.PASSWORD_RESET).count() == 0
    )


def test_a_code_works_once(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    code = start_change(api_client, body)
    confirm(api_client, body["access_token"], code)

    again = confirm(api_client, body["access_token"], code)

    assert again.status_code == 400
    assert first_error(again)["code"] == "code_expired"


def test_a_wrong_code_counts_down_and_then_the_code_is_dead(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    right = start_change(api_client, body)
    wrong = "000000" if right != "000000" else "111111"

    for left in (4, 3, 2, 1, 0):
        response = confirm(api_client, body["access_token"], wrong)
        assert response.status_code == 400
        assert first_error(response)["code"] == "code_invalid"
        assert first_error(response)["attempts_left"] == left
    dead = confirm(api_client, body["access_token"], wrong)

    assert dead.status_code == 429
    assert first_error(dead)["code"] == "code_attempts_exceeded"
    assert int(dead["Retry-After"]) >= 1
    assert confirm(api_client, body["access_token"], right).status_code == 429
    user.refresh_from_db()
    assert user.email != NEW


def test_an_expired_code_is_refused(api_client: APIClient, clock: Clock) -> None:
    user, body = start_session(api_client)
    code = start_change(api_client, body)
    clock.advance(minutes=11)
    token = refresh(api_client, body["refresh_token"]).json()["access_token"]

    response = confirm(api_client, token, code)

    assert first_error(response)["code"] == "code_expired"
    user.refresh_from_db()
    assert user.email != NEW


def test_with_nothing_pending_the_code_is_expired(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = confirm(api_client, body["access_token"], "123456")

    assert response.status_code == 400
    assert first_error(response)["code"] == "code_expired"


@pytest.mark.parametrize("code", ["12345", "1234567", "12345a", ""])
def test_a_malformed_code_is_a_400(api_client: APIClient, code: str) -> None:
    _, body = start_session(api_client)

    response = confirm(api_client, body["access_token"], code)

    assert response.status_code == 400
    assert first_error(response)["attr"] == "code"


def test_an_address_taken_meanwhile_is_a_409_and_keeps_the_code(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    code = start_change(api_client, body)
    VerifiedUserFactory.create(email=NEW)

    response = confirm(api_client, body["access_token"], code)

    assert response.status_code == 409
    assert first_error(response)["code"] == "email_already_registered"
    user.refresh_from_db()
    assert user.email != NEW
    assert EmailCode.objects.count() == 1
    assert EmailCode.objects.get().attempts == 0


def test_a_code_of_another_account_does_not_confirm_this_one(
    api_client: APIClient,
) -> None:
    _, body = start_session(api_client)
    _, other = start_session(api_client)
    request_change(api_client, other["access_token"], "omar.new@example.com")
    others_code = pending_code()

    response = confirm(api_client, body["access_token"], others_code)

    assert first_error(response)["code"] == "code_expired"
    assert EmailCode.objects.get().payload == "omar.new@example.com"
    assert not User.objects.filter(email="omar.new@example.com").exists()


def test_the_change_is_bound_to_the_address_it_was_asked_for(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    code = start_change(api_client, body)
    EmailCode.objects.filter(user=user).update(payload="tampered@example.com")

    confirm(api_client, body["access_token"], code)

    user.refresh_from_db()
    assert (
        user.email == "tampered@example.com"
    )  # the stored address is what counts, never the request


def test_a_session_ended_meanwhile_cannot_confirm(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    code = start_change(api_client, body)
    api_client.post(f"{AUTH}/sign-out/", headers=bearer(body["access_token"]))

    response = confirm(api_client, body["access_token"], code)

    assert first_error(response)["code"] == "session_signed_out"
    user.refresh_from_db()
    assert user.email != NEW


# --- cancel ------------------------------------------------------------------------------------


def test_cancelling_drops_the_pending_change(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    code = start_change(api_client, body)

    response = api_client.delete(CHANGE, headers=bearer(body["access_token"]))

    assert response.status_code == 204
    assert response.content == b""
    assert EmailCode.objects.count() == 0
    assert (
        first_error(confirm(api_client, body["access_token"], code))["code"]
        == "code_expired"
    )
    user.refresh_from_db()
    assert user.email != NEW


def test_cancelling_with_nothing_pending_is_fine(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    assert (
        api_client.delete(CHANGE, headers=bearer(body["access_token"])).status_code
        == 204
    )


def test_cancelling_leaves_another_account_alone(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    _, other = start_session(api_client)
    request_change(api_client, other["access_token"], "omar.new@example.com")

    api_client.delete(CHANGE, headers=bearer(body["access_token"]))

    assert EmailCode.objects.count() == 1


def test_cancelling_a_password_reset_is_not_possible_this_way(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    EmailCode.objects.create(
        user=user,
        purpose=EmailCodePurpose.PASSWORD_RESET,
        code_hash="h",
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    api_client.delete(CHANGE, headers=bearer(body["access_token"]))

    assert EmailCode.objects.count() == 1


# --- access and failure ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", CHANGE, {"email": NEW, "current_password": PASSWORD}),
        ("post", CONFIRM, {"code": "123456"}),
        ("delete", CHANGE, None),
    ],
)
def test_every_call_needs_a_signed_in_user(
    api_client: APIClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    response = getattr(api_client, method)(path, body)

    assert response.status_code == 401


def test_a_limit_store_that_is_down_refuses_the_change(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, body = start_session(api_client)
    monkeypatch.setattr(limits, "cache", BrokenCache())
    api_client.raise_request_exception = False

    response = request_change(api_client, body["access_token"])

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"
    assert mail.outbox == []
    assert EmailCode.objects.count() == 0
    user.refresh_from_db()
    assert user.email != NEW
