"""Helpers for the tests of the sessions list, the session actions and the security activity."""

from typing import Any

from rest_framework.response import Response
from rest_framework.test import APIClient

from users.models import Session, User
from users.tests.helpers import AUTH, bearer, new_device, sign_in

SESSIONS = f"{AUTH}/sessions/"
REVOKE_OTHERS = f"{AUTH}/sessions/revoke-others/"
HISTORY = f"{AUTH}/session-history/"


def session_url(session_id: Any) -> str:
    return f"{SESSIONS}{session_id}/"


def sign_in_on(
    client: APIClient, user: User, **device_fields: Any
) -> tuple[dict[str, Any], Session]:
    """Sign the user in from another phone; returns the answer and the session that made."""
    phone = new_device(**device_fields)
    response = sign_in(client, user.email, device=phone)
    assert response.status_code == 200, response.json()
    body: dict[str, Any] = response.json()
    return body, Session.objects.get(
        user=user, device_id=phone["device_id"], ended_at__isnull=True
    )


def current_session(user: User, body: dict[str, Any]) -> Session:
    """The live session a sign-in answer belongs to (the user's newest)."""
    return Session.objects.filter(user=user, ended_at__isnull=True).latest("created_at")


def get(client: APIClient, url: str, token: str, **params: Any) -> Response:
    return client.get(url, params, headers=bearer(token))


def ids(response: Response) -> list[str]:
    results: list[dict[str, Any]] = response.json()["results"]
    return [item["id"] for item in results]


def walk(client: APIClient, token: str, url: str, **params: Any) -> list[list[str]]:
    """Follow ``next`` from the first page; returns the ids of every page."""
    pages = []
    response = get(client, url, token, **params)
    while True:
        assert response.status_code == 200, response.json()
        pages.append(ids(response))
        link = response.json()["next"]
        if link is None:
            return pages
        response = client.get(link, headers=bearer(token))
