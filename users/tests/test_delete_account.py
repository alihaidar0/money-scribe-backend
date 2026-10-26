"""Deleting the account (ADR 0014): everything goes, at once, after the password is confirmed."""

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
    PasswordResetToken,
    Session,
    User,
)
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    PASSWORD,
    bearer,
    first_error,
    new_device,
    refresh,
    sign_in,
    start_session,
)
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db

DELETE = f"{AUTH}/account/delete/"
PROFILE = f"{AUTH}/profile/"


def delete(client: APIClient, access_token: str, password: str = PASSWORD) -> Response:
    return client.post(DELETE, {"password": password}, headers=bearer(access_token))


def test_the_account_and_everything_it_owns_are_deleted(api_client: APIClient) -> None:
    user, body = start_session(api_client, email="sara@example.com")
    sign_in(api_client, user.email, device=new_device())
    EmailCode.objects.create(
        user=user,
        purpose=EmailCodePurpose.PASSWORD_RESET,
        code_hash="h",
        expires_at=timezone.now(),
    )
    PasswordResetToken.objects.create(
        user=user, token_hash="t" * 64, expires_at=timezone.now()
    )

    response = delete(api_client, body["access_token"])

    assert response.status_code == 204
    assert response.content == b""
    assert not User.objects.filter(pk=user.pk).exists()
    assert Session.objects.count() == 0
    assert EmailCode.objects.count() == 0
    assert PasswordResetToken.objects.count() == 0


def test_every_token_stops_working_at_once(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    other_device = sign_in(api_client, user.email, device=new_device()).json()

    delete(api_client, body["access_token"])

    for pair in (body, other_device):
        profile = api_client.get(PROFILE, headers=bearer(pair["access_token"]))
        assert profile.status_code == 401
        assert first_error(profile)["code"] == "token_invalid"
        renewed = refresh(api_client, pair["refresh_token"])
        assert first_error(renewed)["code"] == "token_invalid"


def test_the_address_is_free_again_and_signing_in_fails(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    delete(api_client, body["access_token"])

    response = sign_in(api_client, user.email)

    assert first_error(response)["code"] == "email_not_registered"
    again = api_client.post(
        f"{AUTH}/register/",
        {"name": "Sara", "email": user.email, "password": "a-fine-passphrase-7"},
    )
    assert again.status_code == 201
    assert User.objects.get(email=user.email).email_verified_at is None


def test_the_address_gets_one_confirmation(api_client: APIClient) -> None:
    user, body = start_session(api_client, language="ar")

    delete(api_client, body["access_token"])

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert "حذف" in mail.outbox[0].subject


def test_a_wrong_password_deletes_nothing(api_client: APIClient) -> None:
    user, body = start_session(api_client)

    response = delete(api_client, body["access_token"], password="not-it-at-all")

    assert response.status_code == 400
    error = first_error(response)
    assert error["code"] == "invalid_password"
    assert error["attr"] == "password"
    assert User.objects.filter(pk=user.pk).exists()
    assert (
        api_client.get(PROFILE, headers=bearer(body["access_token"])).status_code == 200
    )
    assert mail.outbox == []


@pytest.mark.parametrize("body", [{}, {"password": ""}])
def test_a_missing_password_is_a_400(
    api_client: APIClient, body: dict[str, str]
) -> None:
    user, tokens = start_session(api_client)

    response = api_client.post(DELETE, body, headers=bearer(tokens["access_token"]))

    assert response.status_code == 400
    assert User.objects.filter(pk=user.pk).exists()


def test_it_needs_a_signed_in_user(api_client: APIClient) -> None:
    user = VerifiedUserFactory.create()

    response = api_client.post(DELETE, {"password": PASSWORD})

    assert response.status_code == 401
    assert User.objects.filter(pk=user.pk).exists()


def test_an_ended_session_cannot_delete(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    api_client.post(f"{AUTH}/sign-out/", headers=bearer(body["access_token"]))

    response = delete(api_client, body["access_token"])

    assert first_error(response)["code"] == "session_signed_out"
    assert User.objects.filter(pk=user.pk).exists()


def test_only_the_signed_in_account_goes(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    other = VerifiedUserFactory.create()
    other_session = sign_in(api_client, other.email).json()

    delete(api_client, body["access_token"])

    assert not User.objects.filter(pk=user.pk).exists()
    assert User.objects.filter(pk=other.pk).exists()
    assert (
        api_client.get(
            PROFILE, headers=bearer(other_session["access_token"])
        ).status_code
        == 200
    )


def test_the_request_names_no_account_so_none_can_be_chosen(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    other = VerifiedUserFactory.create()

    api_client.post(
        DELETE,
        {"password": PASSWORD, "email": other.email, "user": str(other.pk)},
        headers=bearer(body["access_token"]),
    )

    assert not User.objects.filter(pk=user.pk).exists()
    assert User.objects.filter(pk=other.pk).exists()


def test_five_wrong_passwords_lock_the_deletion(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    token = body["access_token"]
    for _ in range(4):
        assert delete(api_client, token, "wrong").status_code == 400

    fifth = delete(api_client, token, "wrong")
    right = delete(api_client, token)

    assert fifth.status_code == 429
    assert first_error(fifth)["code"] == "password_attempts_exceeded"
    assert fifth["Retry-After"] == "900"
    assert right.status_code == 429
    assert User.objects.filter(pk=user.pk).exists()
    clock.advance(seconds=901)
    token = refresh(api_client, body["refresh_token"]).json()["access_token"]
    assert delete(api_client, token).status_code == 204


def test_the_lock_is_shared_with_the_password_change(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    for _ in range(5):
        api_client.post(
            f"{AUTH}/password/change/",
            {"current_password": "wrong", "password": "a-fine-passphrase-7"},
            headers=bearer(body["access_token"]),
        )

    response = delete(api_client, body["access_token"])

    assert response.status_code == 429
    assert User.objects.filter(pk=user.pk).exists()


def test_a_lock_that_cannot_be_checked_refuses_the_deletion(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, body = start_session(api_client)
    monkeypatch.setattr(limits, "cache", BrokenCache())
    api_client.raise_request_exception = False

    response = delete(api_client, body["access_token"])

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"
    assert User.objects.filter(pk=user.pk).exists()
