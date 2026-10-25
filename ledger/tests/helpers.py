import uuid
from typing import Any

from rest_framework.response import Response
from rest_framework.test import APIClient

from users.tests.helpers import bearer

API = "/api/v1"
ACCOUNTS = f"{API}/accounts/"
CATEGORIES = f"{API}/categories/"

BUILT_IN_KEYS = [
    "groceries",
    "dining",
    "transport",
    "housing",
    "utilities",
    "scholarship",
    "other",
]


def fresh_key() -> str:
    return f'"{uuid.uuid4()}"'


def account_url(account_id: Any) -> str:
    return f"{ACCOUNTS}{account_id}/"


def category_url(category_id: Any) -> str:
    return f"{CATEGORIES}{category_id}/"


def post(
    client: APIClient,
    url: str,
    token: str,
    body: dict[str, Any],
    key: str | None = None,
) -> Response:
    return client.post(
        url,
        body,
        headers={**bearer(token), "Idempotency-Key": key or fresh_key()},
    )


def add_account(
    client: APIClient, token: str, name: str = "Travel card", kind: str = "card"
) -> Response:
    return post(client, ACCOUNTS, token, {"name": name, "kind": kind})


def add_category(
    client: APIClient, token: str, name: str = "Pets", icon: str = "gifts"
) -> Response:
    return post(client, CATEGORIES, token, {"name": name, "icon": icon})


def patch(
    client: APIClient, url: str, token: str, body: dict[str, Any], **headers: str
) -> Response:
    return client.patch(url, body, headers={**bearer(token), **headers})


def delete(client: APIClient, url: str, token: str, **headers: str) -> Response:
    return client.delete(url, headers={**bearer(token), **headers})


def get(client: APIClient, url: str, token: str, **params: Any) -> Response:
    return client.get(url, params, headers=bearer(token))


def names(response: Response) -> list[str]:
    return [item["name"] for item in response.json()["results"]]
