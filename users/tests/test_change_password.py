"""Changing the password while signed in (ADR 0014)."""

from typing import Any

import pytest
from django.core import mail
from rest_framework.response import Response
from rest_framework.test import APIClient

from conftest import Clock
from users import limits
from users.models import EndReason, Session, User
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    PASSWORD,
    bearer,
    first_error,
    refresh,
    sign_in,
    start_session,
)
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db

CHANGE = f"{AUTH}/password/change/"
PROFILE = f"{AUTH}/profile/"
NEW_PASSWORD = "a-fine-passphrase-7"


def change(
    client: APIClient,
    access_token: str,
    password: str = NEW_PASSWORD,
    current_password: str = PASSWORD,
) -> Response:
    return client.post(
        CHANGE,
        {"current_password": current_password, "password": password},
        headers=bearer(access_token),
    )


def test_the_password_changes_and_the_session_continues_with_new_tokens(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)

    response = change(api_client, body["access_token"])

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    pair = response.json()
    assert set(pair) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert pair["token_type"] == "Bearer"
    assert pair["refresh_token"] != body["refresh_token"]
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(PASSWORD)
    assert (
        api_client.get(PROFILE, headers=bearer(pair["access_token"])).status_code == 200
    )
    assert refresh(api_client, pair["refresh_token"]).status_code == 200


def test_the_old_refresh_token_is_retired(api_client: APIClient, clock: Clock) -> None:
    _, body = start_session(api_client)
    pair = change(api_client, body["access_token"]).json()
    clock.advance(seconds=31)

    replay = refresh(api_client, body["refresh_token"])

    assert replay.status_code == 401
    assert first_error(replay)["code"] == "session_token_reuse"
    assert refresh(api_client, pair["refresh_token"]).status_code == 401


def test_every_other_session_ends_and_says_why(api_client: APIClient) -> None:
    user, first = start_session(api_client)
    second = sign_in(api_client, user.email).json()
    third = sign_in(api_client, user.email).json()

    change(api_client, first["access_token"])

    ended = Session.objects.filter(ended_at__isnull=False)
    assert ended.count() == 2
    assert {item.end_reason for item in ended} == {EndReason.PASSWORD_CHANGED}
    assert Session.objects.filter(ended_at__isnull=True).count() == 1
    for other in (second, third):
        profile = api_client.get(PROFILE, headers=bearer(other["access_token"]))
        assert first_error(profile)["code"] == "session_password_changed"
        assert (
            first_error(refresh(api_client, other["refresh_token"]))["code"]
            == "session_password_changed"
        )


def test_the_owner_is_told_about_the_change(api_client: APIClient) -> None:
    user, body = start_session(api_client, language="ar")

    change(api_client, body["access_token"])

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert "كلمة المرور" in mail.outbox[0].subject


def test_a_wrong_current_password_is_a_400_not_a_401(api_client: APIClient) -> None:
    user, body = start_session(api_client)

    response = change(
        api_client, body["access_token"], current_password="not-it-at-all"
    )

    assert response.status_code == 400
    error = first_error(response)
    assert error["code"] == "invalid_password"
    assert error["attr"] == "current_password"
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
    assert (
        api_client.get(PROFILE, headers=bearer(body["access_token"])).status_code == 200
    )


def test_the_same_password_is_refused(api_client: APIClient) -> None:
    user, body = start_session(api_client)

    response = change(api_client, body["access_token"], password=PASSWORD)

    assert response.status_code == 400
    error = first_error(response)
    assert error["code"] == "password_unchanged"
    assert error["attr"] == "password"
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_the_same_password_is_refused_before_the_rules_are_applied(
    api_client: APIClient,
) -> None:
    """The current password may predate the rules (it is 12 characters long), yet the answer is one error."""
    _, body = start_session(api_client)

    response = change(api_client, body["access_token"], password=PASSWORD)

    assert [item["code"] for item in response.json()["errors"]] == [
        "password_unchanged"
    ]


def test_the_same_password_typed_another_way_is_still_the_same(
    api_client: APIClient,
) -> None:
    """Passwords are NFKC-normalized, so a full-width form of the current one is the current one."""
    _, body = start_session(api_client)
    fullwidth = "".join(
        chr(ord(c) + 0xFEE0) for c in PASSWORD
    )  # ASCII to full-width forms

    response = change(api_client, body["access_token"], password=fullwidth)

    assert first_error(response)["code"] == "password_unchanged"


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("short", "password_too_short"),
        ("x" * 129, "password_too_long"),
        ("1q2w3e4r5t6y7u8i9o0p", "password_too_common"),
    ],
)
def test_the_password_rules_apply(
    api_client: APIClient, password: str, code: str
) -> None:
    _, body = start_session(api_client)

    response = change(api_client, body["access_token"], password=password)

    assert response.status_code == 400
    assert code in {item["code"] for item in response.json()["errors"]}
    assert {item["attr"] for item in response.json()["errors"]} == {"password"}


def test_the_length_limits_ride_with_the_error(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    short = first_error(change(api_client, body["access_token"], password="short"))

    assert short["min_length"] == 15


def test_a_password_like_the_email_is_refused(api_client: APIClient) -> None:
    _, body = start_session(api_client, email="sara@example.com")

    response = change(api_client, body["access_token"], password="sara@example.com!!!!")

    assert first_error(response)["code"] == "password_too_similar"


def test_a_compromised_password_is_refused(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.PWNED_PASSWORD_CHECK = True
    monkeypatch.setattr("users.validators.breach_count", lambda password: 9)
    _, body = start_session(api_client)

    response = change(api_client, body["access_token"])

    assert first_error(response)["code"] == "password_compromised"


def test_a_wrong_current_password_hides_the_rules(api_client: APIClient) -> None:
    """The new password is judged only after the current one is proved."""
    _, body = start_session(api_client)

    response = change(
        api_client, body["access_token"], password="short", current_password="wrong"
    )

    assert [item["code"] for item in response.json()["errors"]] == ["invalid_password"]


@pytest.mark.parametrize(
    "body", [{}, {"password": NEW_PASSWORD}, {"current_password": PASSWORD}]
)
def test_missing_fields_are_a_400(api_client: APIClient, body: dict[str, str]) -> None:
    _, tokens = start_session(api_client)

    response = api_client.post(CHANGE, body, headers=bearer(tokens["access_token"]))

    assert response.status_code == 400


def test_it_needs_a_signed_in_user(api_client: APIClient) -> None:
    response = api_client.post(
        CHANGE, {"current_password": PASSWORD, "password": NEW_PASSWORD}
    )

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"


def test_an_ended_session_cannot_change_the_password(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    api_client.post(f"{AUTH}/sign-out/", headers=bearer(body["access_token"]))

    response = change(api_client, body["access_token"])

    assert first_error(response)["code"] == "session_signed_out"
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_another_user_is_not_touched(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    other = VerifiedUserFactory.create()
    other_session = sign_in(api_client, other.email).json()

    change(api_client, body["access_token"])

    other.refresh_from_db()
    assert other.check_password(PASSWORD)
    assert (
        api_client.get(
            PROFILE, headers=bearer(other_session["access_token"])
        ).status_code
        == 200
    )


def test_the_password_in_the_body_decides_nothing_about_who_changes(
    api_client: APIClient,
) -> None:
    """There is no user id in the request, so the account is always the signed-in one."""
    user, body = start_session(api_client)
    other = VerifiedUserFactory.create()

    api_client.post(
        CHANGE,
        {
            "current_password": PASSWORD,
            "password": NEW_PASSWORD,
            "email": other.email,
            "user": str(other.pk),
        },
        headers=bearer(body["access_token"]),
    )

    other.refresh_from_db()
    user.refresh_from_db()
    assert other.check_password(PASSWORD)
    assert user.check_password(NEW_PASSWORD)


# --- the lock on wrong current passwords ------------------------------------------------------------


def test_five_wrong_passwords_lock_the_operation(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    token = body["access_token"]

    for _ in range(4):
        assert change(api_client, token, current_password="wrong").status_code == 400
    fifth = change(api_client, token, current_password="wrong")

    assert fifth.status_code == 429
    assert first_error(fifth)["code"] == "password_attempts_exceeded"
    assert fifth["Retry-After"] == "900"
    clock.advance(seconds=100)
    right = change(api_client, token)
    assert right.status_code == 429
    assert right["Retry-After"] == "800"
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_the_lock_ends_and_a_right_password_clears_the_count(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    token = body["access_token"]
    for _ in range(5):
        change(api_client, token, current_password="wrong")
    clock.advance(seconds=901)
    token = refresh(api_client, body["refresh_token"]).json()["access_token"]

    assert change(api_client, token, current_password="wrong").status_code == 400
    assert change(api_client, token).status_code == 200


def test_a_right_password_starts_the_count_again(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    token = body["access_token"]
    for _ in range(4):
        change(api_client, token, current_password="wrong")
    # the right password, but refused by a rule: it still proves the password
    assert change(api_client, token, password="short").status_code == 400

    for _ in range(4):
        assert change(api_client, token, current_password="wrong").status_code == 400


def test_the_lock_leaves_sign_in_alone(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    for _ in range(5):
        change(api_client, body["access_token"], current_password="wrong")

    assert sign_in(api_client, user.email).status_code == 200


def test_the_lock_is_per_account(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    _, other = start_session(api_client)
    for _ in range(5):
        change(api_client, body["access_token"], current_password="wrong")

    assert change(api_client, other["access_token"]).status_code == 200


def test_a_lock_that_cannot_be_checked_refuses_the_change(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, body = start_session(api_client)
    monkeypatch.setattr(limits, "cache", BrokenCache())
    api_client.raise_request_exception = False

    response = change(api_client, body["access_token"])

    assert response.status_code == 503
    assert response["Retry-After"] == "30"
    assert first_error(response)["code"] == "service_unavailable"
    user.refresh_from_db()
    assert user.check_password(PASSWORD)
    assert User.objects.count() == 1
