from typing import Any

from rest_framework.response import Response
from rest_framework.test import APIClient

from users.tests.helpers import bearer

URL = "/api/v1/notes/"
KEY = '"8e03978e-40d5-43e8-bc93-6894a57f9324"'
OTHER_KEY = '"0f4d6c2a-91b3-4c58-a7e2-5d3b1c8f9a60"'


def create(
    client: APIClient,
    token: str,
    body: dict[str, Any] | None = None,
    key: str | None = KEY,
    url: str = URL,
) -> Response:
    headers = dict(bearer(token))
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        url, {"title": "rent"} if body is None else body, headers=headers
    )


def detail(note_id: str) -> str:
    return f"{URL}{note_id}/"
