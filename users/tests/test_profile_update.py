"""Editing the display name (ADR 0015); nothing else of the profile can be changed here."""

from typing import Any

import pytest
from rest_framework.response import Response
from rest_framework.test import APIClient

from users.models import User
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import AUTH, bearer, first_error, start_session

pytestmark = pytest.mark.django_db

PROFILE = f"{AUTH}/profile/"


def patch(client: APIClient, access_token: str, body: dict[str, Any]) -> Response:
    return client.patch(PROFILE, body, headers=bearer(access_token))


def test_the_name_is_changed_and_the_profile_returned(api_client: APIClient) -> None:
    user, body = start_session(api_client, language="ar", time_zone="Asia/Riyadh")

    response = patch(api_client, body["access_token"], {"name": "Sara Ahmed"})

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.pk),
        "name": "Sara Ahmed",
        "email": user.email,
        "language": "ar",
        "time_zone": "Asia/Riyadh",
    }
    user.refresh_from_db()
    assert user.name == "Sara Ahmed"
    read = api_client.get(PROFILE, headers=bearer(body["access_token"]))
    assert read.json()["name"] == "Sara Ahmed"


def test_the_name_is_trimmed(api_client: APIClient) -> None:
    user, body = start_session(api_client)

    response = patch(api_client, body["access_token"], {"name": "  Sara  "})

    assert response.json()["name"] == "Sara"
    user.refresh_from_db()
    assert user.name == "Sara"


def test_any_unicode_name_is_allowed(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = patch(api_client, body["access_token"], {"name": "سارة أحمد"})

    assert response.json()["name"] == "سارة أحمد"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, "required"),
        ({"name": ""}, "blank"),
        ({"name": "   "}, "blank"),
        ({"name": "x" * 101}, "max_length"),
        ({"name": None}, "null"),
    ],
)
def test_a_bad_name_is_a_400(
    api_client: APIClient, body: dict[str, Any], code: str
) -> None:
    user, tokens = start_session(api_client)
    before = user.name

    response = patch(api_client, tokens["access_token"], body)

    assert response.status_code == 400
    error = first_error(response)
    assert error["code"] == code
    assert error["attr"] == "name"
    user.refresh_from_db()
    assert user.name == before


def test_a_name_of_a_hundred_characters_is_fine(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    assert (
        patch(api_client, body["access_token"], {"name": "x" * 100}).status_code == 200
    )


@pytest.mark.parametrize(
    "field", ["email", "language", "time_zone", "id", "email_verified_at", "is_staff"]
)
def test_another_field_is_refused_not_ignored(
    api_client: APIClient, field: str
) -> None:
    user, body = start_session(api_client)
    before = (user.name, user.email, user.language, user.time_zone, user.is_staff)

    response = patch(
        api_client, body["access_token"], {"name": "Sara", field: "changed@example.com"}
    )

    assert response.status_code == 400
    error = first_error(response)
    assert error["code"] == "read_only"
    assert error["attr"] == field
    user.refresh_from_db()
    assert (
        user.name,
        user.email,
        user.language,
        user.time_zone,
        user.is_staff,
    ) == before


def test_it_needs_a_signed_in_user(api_client: APIClient) -> None:
    response = api_client.patch(PROFILE, {"name": "Sara"})

    assert response.status_code == 401


def test_only_the_signed_in_user_changes(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    other = VerifiedUserFactory.create(name="Omar")

    patch(api_client, body["access_token"], {"name": "Sara", "id": str(other.pk)})
    patch(api_client, body["access_token"], {"name": "Sara"})

    other.refresh_from_db()
    assert other.name == "Omar"
    assert User.objects.filter(name="Sara").count() == 1
