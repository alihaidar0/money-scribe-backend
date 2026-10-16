import pytest
from rest_framework.test import APIClient

from conftest import Clock
from users import services, tokens
from users.models import EndReason, Session
from users.tests.helpers import AUTH, bearer, first_error, refresh, start_session

pytestmark = pytest.mark.django_db


def test_a_refresh_rotates_the_token_and_returns_a_new_pair(
    api_client: APIClient, clock: Clock
) -> None:
    _, first = start_session(api_client)
    clock.advance(minutes=5)

    response = refresh(api_client, first["refresh_token"])

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert body["refresh_token"] != first["refresh_token"]
    assert response["Cache-Control"] == "no-store"
    session = Session.objects.get()
    assert session.generation == 1
    assert session.refresh_hash == tokens.hash_token(body["refresh_token"])
    assert tokens.decode_refresh_token(body["refresh_token"]).generation == 1


def test_the_new_access_token_works_and_the_chain_continues(
    api_client: APIClient, clock: Clock
) -> None:
    _, first = start_session(api_client)
    token = first["refresh_token"]

    for generation in range(1, 4):
        clock.advance(minutes=9)
        body = refresh(api_client, token).json()
        token = body["refresh_token"]
        assert tokens.decode_refresh_token(token).generation == generation
        assert (
            api_client.get(
                f"{AUTH}/profile/", headers=bearer(body["access_token"])
            ).status_code
            == 200
        )


def test_a_lost_answer_can_be_retried_and_returns_the_same_successor(
    api_client: APIClient, clock: Clock
) -> None:
    _, first = start_session(api_client)
    clock.advance(minutes=5)
    answer = refresh(api_client, first["refresh_token"]).json()
    clock.advance(seconds=10)

    retry = refresh(api_client, first["refresh_token"])

    assert retry.status_code == 200
    assert retry.json()["refresh_token"] == answer["refresh_token"]
    assert retry.json()["access_token"] != answer["access_token"]
    assert Session.objects.get().generation == 1
    assert refresh(api_client, answer["refresh_token"]).status_code == 200


def test_the_replay_window_counts_from_the_rotation_and_is_30_seconds(
    api_client: APIClient, clock: Clock
) -> None:
    _, first = start_session(api_client)
    clock.advance(minutes=5)
    answer = refresh(api_client, first["refresh_token"]).json()

    clock.advance(seconds=30)
    assert (
        refresh(api_client, first["refresh_token"]).json()["refresh_token"]
        == answer["refresh_token"]
    )
    clock.advance(seconds=1)
    late = refresh(api_client, first["refresh_token"])

    assert late.status_code == 401
    assert first_error(late)["code"] == "session_token_reuse"


def test_a_used_token_after_the_window_ends_the_whole_session(
    api_client: APIClient, clock: Clock
) -> None:
    _, first = start_session(api_client)
    clock.advance(minutes=5)
    newest = refresh(api_client, first["refresh_token"]).json()
    clock.advance(seconds=31)

    response = refresh(api_client, first["refresh_token"])

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert first_error(response)["code"] == "session_token_reuse"
    session = Session.objects.get()
    assert session.ended_at == clock.current
    assert session.end_reason == EndReason.TOKEN_REUSE
    # The legitimate holder of the newest token is out as well, and so is its access token.
    assert (
        first_error(refresh(api_client, newest["refresh_token"]))["code"]
        == "session_token_reuse"
    )
    profile = api_client.get(f"{AUTH}/profile/", headers=bearer(newest["access_token"]))
    assert first_error(profile)["code"] == "session_token_reuse"


def test_a_token_two_generations_old_is_a_replay_even_inside_the_window(
    api_client: APIClient, clock: Clock
) -> None:
    _, first = start_session(api_client)
    second = refresh(api_client, first["refresh_token"]).json()
    refresh(api_client, second["refresh_token"])

    response = refresh(api_client, first["refresh_token"])

    assert response.status_code == 401
    assert first_error(response)["code"] == "session_token_reuse"
    assert Session.objects.get().end_reason == EndReason.TOKEN_REUSE


def test_the_refresh_updates_the_device_and_the_activity(
    api_client: APIClient, clock: Clock
) -> None:
    _, first = start_session(api_client)
    clock.advance(hours=1)

    api_client.post(
        f"{AUTH}/refresh/",
        {
            "refresh_token": first["refresh_token"],
            "device": {
                "device_id": "0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d",
                "device_name": "Pixel 8 Pro",
                "platform": "android",
                "os_version": "Android 16",
                "app_version": "1.2.0+7",
            },
        },
        REMOTE_ADDR="203.0.113.9",
    )

    session = Session.objects.get()
    assert (session.device_name, session.os_version, session.app_version) == (
        "Pixel 8 Pro",
        "Android 16",
        "1.2.0+7",
    )
    assert session.last_active_at == clock.current
    assert session.ip == "203.0.113.0"


@pytest.mark.parametrize("token", ["garbage", "a.b.c"])
def test_garbage_is_token_invalid(api_client: APIClient, token: str) -> None:
    start_session(api_client)

    response = refresh(api_client, token)

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert first_error(response)["code"] == "token_invalid"


def test_an_access_token_is_not_a_refresh_token(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = refresh(api_client, body["access_token"])

    assert first_error(response)["code"] == "token_invalid"


def test_the_refresh_token_is_required(api_client: APIClient) -> None:
    response = api_client.post(f"{AUTH}/refresh/", {})

    assert response.status_code == 400
    assert {error["attr"] for error in response.json()["errors"]} == {
        "refresh_token",
        "device",
    }


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        (EndReason.SIGNED_OUT, "session_signed_out"),
        (EndReason.REVOKED_BY_USER, "session_revoked_by_user"),
        (EndReason.PASSWORD_CHANGED, "session_password_changed"),
        (EndReason.EMAIL_CHANGED, "session_email_changed"),
        (EndReason.EXPIRED, "session_expired"),
        (EndReason.ACCOUNT_DELETED, "token_invalid"),
    ],
)
def test_an_ended_session_says_why(
    api_client: APIClient, reason: EndReason, code: str
) -> None:
    _, body = start_session(api_client)
    services.end_session(Session.objects.get(), reason)

    response = refresh(api_client, body["refresh_token"])

    assert response.status_code == 401
    assert first_error(response)["code"] == code


def test_a_refresh_token_unused_for_30_days_has_expired_and_ends_its_session(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    clock.advance(days=30, seconds=1)

    response = refresh(api_client, body["refresh_token"])

    assert response.status_code == 401
    assert first_error(response)["code"] == "session_expired"
    session = Session.objects.get()
    assert session.end_reason == EndReason.EXPIRED


def test_the_session_ends_90_days_after_sign_in_however_active(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    token = body["refresh_token"]

    for _ in range(4):
        clock.advance(days=20)
        response = refresh(api_client, token)
        assert response.status_code == 200
        token = response.json()["refresh_token"]
    # Day 80: the newest token is capped at day 90 instead of living 30 more days.
    payload = tokens.decode_refresh_token(token)
    assert payload.generation == 4
    clock.advance(days=11)

    response = refresh(api_client, token)

    assert first_error(response)["code"] == "session_expired"


def test_a_session_past_its_limit_is_refused_even_with_a_live_token(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    Session.objects.update(expires_at=clock.current)

    response = refresh(api_client, body["refresh_token"])

    assert first_error(response)["code"] == "session_expired"


def test_an_inactive_user_cannot_refresh(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    user.is_active = False
    user.save()

    response = refresh(api_client, body["refresh_token"])

    assert first_error(response)["code"] == "token_invalid"


def test_a_deleted_session_is_token_invalid(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    Session.objects.all().delete()

    assert (
        first_error(refresh(api_client, body["refresh_token"]))["code"]
        == "token_invalid"
    )


def test_a_stale_header_cannot_break_a_refresh(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    payload = {
        "refresh_token": body["refresh_token"],
        "device": {
            "device_id": "0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d",
            "device_name": "Pixel 8",
            "platform": "android",
            "app_version": "1",
        },
    }

    response = api_client.post(
        f"{AUTH}/refresh/", payload, headers=bearer("expired-garbage")
    )

    assert response.status_code == 200
