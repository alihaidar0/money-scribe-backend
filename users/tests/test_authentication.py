import pytest
from rest_framework.test import APIClient

from conftest import Clock
from users import services
from users.models import EndReason, Session
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    bearer,
    device,
    first_error,
    sign_in,
    start_session,
)

pytestmark = pytest.mark.django_db

PROFILE = f"{AUTH}/profile/"


def test_a_valid_access_token_reaches_the_profile(api_client: APIClient) -> None:
    user, body = start_session(
        api_client, name="Sara Example", language="ar", time_zone="Asia/Riyadh"
    )

    response = api_client.get(PROFILE, headers=bearer(body["access_token"]))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "name": "Sara Example",
        "email": user.email,
        "language": "ar",
        "time_zone": "Asia/Riyadh",
    }


def test_every_user_reads_only_their_own_profile(api_client: APIClient) -> None:
    first, first_body = start_session(api_client)
    second, second_body = start_session(api_client)

    one = api_client.get(PROFILE, headers=bearer(first_body["access_token"])).json()
    two = api_client.get(PROFILE, headers=bearer(second_body["access_token"])).json()

    assert one["id"] == str(first.id)
    assert two["id"] == str(second.id)
    assert one["email"] != two["email"]


def test_the_profile_cannot_be_written_here(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = api_client.patch(
        PROFILE, {"name": "Changed"}, headers=bearer(body["access_token"])
    )

    assert response.status_code == 405


def test_no_token_is_a_401_with_the_bearer_challenge(api_client: APIClient) -> None:
    response = api_client.get(PROFILE)

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert first_error(response)["code"] == "not_authenticated"


def test_another_scheme_is_treated_as_no_token(api_client: APIClient) -> None:
    response = api_client.get(PROFILE, headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert response.status_code == 401
    assert first_error(response)["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "header", ["Bearer", "Bearer a b", "Bearer garbage", "bearer a.b.c"]
)
def test_a_bad_token_is_token_invalid(api_client: APIClient, header: str) -> None:
    response = api_client.get(PROFILE, headers={"Authorization": header})

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert first_error(response)["code"] == "token_invalid"


def test_the_scheme_name_is_case_insensitive(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = api_client.get(
        PROFILE, headers={"Authorization": f"bearer {body['access_token']}"}
    )

    assert response.status_code == 200


def test_an_expired_access_token_says_so_so_the_app_refreshes(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    clock.advance(seconds=599)
    assert (
        api_client.get(PROFILE, headers=bearer(body["access_token"])).status_code == 200
    )
    clock.advance(seconds=1)

    response = api_client.get(PROFILE, headers=bearer(body["access_token"]))

    assert response.status_code == 401
    assert first_error(response)["code"] == "token_expired"


def test_a_refresh_token_is_not_accepted_as_an_access_token(
    api_client: APIClient,
) -> None:
    _, body = start_session(api_client)

    response = api_client.get(PROFILE, headers=bearer(body["refresh_token"]))

    assert first_error(response)["code"] == "token_invalid"


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        (EndReason.SIGNED_OUT, "session_signed_out"),
        (EndReason.REVOKED_BY_USER, "session_revoked_by_user"),
        (EndReason.PASSWORD_CHANGED, "session_password_changed"),
        (EndReason.EMAIL_CHANGED, "session_email_changed"),
        (EndReason.TOKEN_REUSE, "session_token_reuse"),
        (EndReason.EXPIRED, "session_expired"),
        (EndReason.ACCOUNT_DELETED, "token_invalid"),
    ],
)
def test_an_ended_session_cuts_access_at_once_and_names_the_reason(
    api_client: APIClient, reason: EndReason, code: str
) -> None:
    _, body = start_session(api_client)
    assert (
        api_client.get(PROFILE, headers=bearer(body["access_token"])).status_code == 200
    )

    services.end_session(Session.objects.get(), reason)

    response = api_client.get(PROFILE, headers=bearer(body["access_token"]))
    assert response.status_code == 401
    assert first_error(response)["code"] == code


def test_ending_one_session_leaves_the_users_other_sessions_alone(
    api_client: APIClient,
) -> None:
    user, first = start_session(api_client)
    second = sign_in(api_client, user.email, device=device(device_name="Tablet")).json()
    services.end_session(
        Session.objects.order_by("created_at")[0], EndReason.REVOKED_BY_USER
    )

    assert (
        api_client.get(PROFILE, headers=bearer(first["access_token"])).status_code
        == 401
    )
    assert (
        api_client.get(PROFILE, headers=bearer(second["access_token"])).status_code
        == 200
    )


def test_a_session_past_its_limit_is_refused_with_a_live_token(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    Session.objects.update(expires_at=clock.current)

    response = api_client.get(PROFILE, headers=bearer(body["access_token"]))

    assert first_error(response)["code"] == "session_expired"


def test_an_inactive_user_is_refused(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    user.is_active = False
    user.save()

    response = api_client.get(PROFILE, headers=bearer(body["access_token"]))

    assert first_error(response)["code"] == "token_invalid"


def test_a_deleted_session_is_refused(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    Session.objects.all().delete()

    assert (
        first_error(api_client.get(PROFILE, headers=bearer(body["access_token"])))[
            "code"
        ]
        == "token_invalid"
    )


def test_a_deleted_account_takes_its_sessions_with_it(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    user.delete()

    assert Session.objects.count() == 0
    assert (
        first_error(api_client.get(PROFILE, headers=bearer(body["access_token"])))[
            "code"
        ]
        == "token_invalid"
    )


def test_activity_is_recorded_at_most_every_five_minutes(
    api_client: APIClient, clock: Clock
) -> None:
    _, body = start_session(api_client)
    signed_in_at = Session.objects.get().last_active_at

    clock.advance(minutes=4)
    api_client.get(PROFILE, headers=bearer(body["access_token"]))
    assert Session.objects.get().last_active_at == signed_in_at

    clock.advance(minutes=1)
    api_client.get(
        PROFILE, headers=bearer(body["access_token"]), REMOTE_ADDR="203.0.113.50"
    )
    session = Session.objects.get()
    assert session.last_active_at == clock.current
    assert session.ip == "203.0.113.0"


def test_a_user_who_was_never_signed_in_has_no_access(api_client: APIClient) -> None:
    VerifiedUserFactory.create()

    assert api_client.get(PROFILE).status_code == 401
