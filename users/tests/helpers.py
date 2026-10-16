import re
from typing import Any

from django.core import mail
from rest_framework.response import Response
from rest_framework.test import APIClient

AUTH = "/api/v1/auth"
PASSWORD = "valid-pass-1"
DEVICE_ID = "0b8f7c1e-6d2a-4c53-9a3e-1f2d4b5c6a7d"


def device(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "device_id": DEVICE_ID,
        "device_name": "Pixel 8",
        "platform": "android",
        "os_version": "Android 15",
        "app_version": "1.0.0+1",
    }
    return {**body, **overrides}


def emailed_code() -> str:
    """The 6-digit code in the last email sent."""
    match = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert match is not None
    return match.group(1)


def sign_in(
    client: APIClient, email: str, password: str = PASSWORD, **extra: Any
) -> Response:
    body = {"email": email, "password": password, "device": device(), **extra}
    return client.post(f"{AUTH}/sign-in/", body)


def tokens_for(client: APIClient, email: str) -> dict[str, Any]:
    """Sign a verified user in and return the answer body (tokens and user)."""
    response = sign_in(client, email)
    assert response.status_code == 200, response.json()
    body: dict[str, Any] = response.json()
    return body


def bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def first_error(response: Response) -> dict[str, Any]:
    error: dict[str, Any] = response.json()["errors"][0]
    return error


def start_session(client: APIClient, **user_fields: Any) -> tuple[Any, dict[str, Any]]:
    """A verified user with a live session: returns the user and the sign-in answer."""
    from users.tests.factories import VerifiedUserFactory

    user = VerifiedUserFactory.create(**user_fields)
    return user, tokens_for(client, user.email)


def refresh(client: APIClient, refresh_token: str, **device_fields: Any) -> Response:
    return client.post(
        f"{AUTH}/refresh/",
        {"refresh_token": refresh_token, "device": device(**device_fields)},
    )
