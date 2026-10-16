import pytest
from rest_framework.test import APIClient

from conftest import Clock
from users.models import EndReason, Session
from users.tests.helpers import (
    AUTH,
    bearer,
    first_error,
    refresh,
    sign_in,
    start_session,
)

pytestmark = pytest.mark.django_db

SIGN_OUT = f"{AUTH}/sign-out/"
PROFILE = f"{AUTH}/profile/"


def test_the_access_token_signs_the_session_out_at_once(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = api_client.post(SIGN_OUT, headers=bearer(body["access_token"]))

    assert response.status_code == 204
    assert response.content == b""
    session = Session.objects.get()
    assert session.end_reason == EndReason.SIGNED_OUT
    assert session.ended_at is not None
    after = api_client.get(PROFILE, headers=bearer(body["access_token"]))
    assert first_error(after)["code"] == "session_signed_out"
    assert (
        first_error(refresh(api_client, body["refresh_token"]))["code"]
        == "session_signed_out"
    )


def test_only_the_calling_session_ends(api_client: APIClient) -> None:
    user, first = start_session(api_client)
    second = sign_in(api_client, user.email).json()

    api_client.post(SIGN_OUT, headers=bearer(first["access_token"]))

    assert (
        api_client.get(PROFILE, headers=bearer(second["access_token"])).status_code
        == 200
    )
    assert Session.objects.filter(ended_at__isnull=True).count() == 1


def test_the_refresh_token_in_the_body_signs_out_when_the_access_token_has_expired(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    clock.advance(minutes=11)

    response = api_client.post(
        SIGN_OUT,
        {"refresh_token": body["refresh_token"]},
        headers=bearer(body["access_token"]),
    )

    assert response.status_code == 204
    assert Session.objects.get().end_reason == EndReason.SIGNED_OUT


def test_the_refresh_token_alone_is_enough(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = api_client.post(SIGN_OUT, {"refresh_token": body["refresh_token"]})

    assert response.status_code == 204
    assert Session.objects.get().ended_at is not None


def test_a_valid_access_token_wins_over_a_refresh_token_of_another_session(
    api_client: APIClient,
) -> None:
    user, first = start_session(api_client)
    second = sign_in(api_client, user.email).json()

    api_client.post(
        SIGN_OUT,
        {"refresh_token": second["refresh_token"]},
        headers=bearer(first["access_token"]),
    )

    ended = Session.objects.get(ended_at__isnull=False)
    assert (
        api_client.get(PROFILE, headers=bearer(second["access_token"])).status_code
        == 200
    )
    assert ended.end_reason == EndReason.SIGNED_OUT


def test_an_expired_access_token_alone_asks_the_app_to_refresh(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    clock.advance(minutes=11)

    response = api_client.post(SIGN_OUT, headers=bearer(body["access_token"]))

    assert response.status_code == 401
    assert first_error(response)["code"] == "token_expired"
    assert Session.objects.get().ended_at is None


def test_nothing_valid_is_token_invalid(api_client: APIClient) -> None:
    start_session(api_client)

    for response in (
        api_client.post(SIGN_OUT),
        api_client.post(SIGN_OUT, {"refresh_token": "garbage"}),
        api_client.post(SIGN_OUT, headers=bearer("garbage")),
    ):
        assert response.status_code == 401
        assert response["WWW-Authenticate"] == "Bearer"
        assert first_error(response)["code"] == "token_invalid"
    assert Session.objects.get().ended_at is None


def test_signing_out_twice_is_harmless(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    api_client.post(SIGN_OUT, {"refresh_token": body["refresh_token"]})
    ended_at = Session.objects.get().ended_at

    response = api_client.post(SIGN_OUT, {"refresh_token": body["refresh_token"]})

    assert response.status_code == 204
    assert Session.objects.get().ended_at == ended_at


def test_an_access_token_is_not_accepted_as_the_refresh_token(
    api_client: APIClient,
) -> None:
    _, body = start_session(api_client)

    response = api_client.post(SIGN_OUT, {"refresh_token": body["access_token"]})

    assert first_error(response)["code"] == "token_invalid"
    assert Session.objects.get().ended_at is None
