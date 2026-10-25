"""The text rules of ADR 0021 on the names a person types at registration and in the profile."""

import pytest
from rest_framework.test import APIClient

from users.models import User
from users.tests.helpers import AUTH, bearer, device, first_error, start_session

pytestmark = pytest.mark.django_db

PROFILE = f"{AUTH}/profile/"
BAD_NAMES = ["Sam\nEvil", "Sam\tEvil", "Sam\u202eEvil", "Sam\u2066Evil", "Sam\x00"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_the_profile_name_refuses_a_forbidden_character(
    api_client: APIClient, name: str
) -> None:
    user, body = start_session(api_client)

    response = api_client.patch(
        PROFILE, {"name": name}, headers=bearer(body["access_token"])
    )

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("invalid_characters", "name")
    user.refresh_from_db()
    assert user.name != name


@pytest.mark.parametrize("name", BAD_NAMES)
def test_registration_refuses_a_forbidden_character_in_the_name(
    api_client: APIClient, name: str
) -> None:
    response = api_client.post(
        f"{AUTH}/register/",
        {
            "name": name,
            "email": "sara@example.com",
            "password": "a-fine-passphrase-7",
        },
    )

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("invalid_characters", "name")
    assert not User.objects.filter(email="sara@example.com").exists()


def test_the_name_is_stored_normalized_to_nfc(api_client: APIClient) -> None:
    user, body = start_session(api_client)

    response = api_client.patch(
        PROFILE,
        {"name": "  Renée  "},
        headers=bearer(body["access_token"]),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renée"
    user.refresh_from_db()
    assert user.name == "Renée"


def test_a_name_that_is_not_text_is_refused(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = api_client.patch(
        PROFILE, {"name": 12345}, headers=bearer(body["access_token"])
    )

    assert response.status_code == 400
    assert first_error(response)["attr"] == "name"


def test_the_label_of_a_device_is_left_alone(api_client: APIClient) -> None:
    """The device label is built by the app, not typed by the person: the rule does not apply to it (ADR 0021)."""
    from users.tests.factories import VerifiedUserFactory

    user = VerifiedUserFactory.create()

    response = api_client.post(
        f"{AUTH}/sign-in/",
        {
            "email": user.email,
            "password": "valid-pass-1",
            "device": device(device_name="Pixel\n8"),
        },
    )

    assert response.status_code == 200
