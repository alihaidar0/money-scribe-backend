import uuid
from typing import Any

from rest_framework.response import Response
from rest_framework.test import APIClient

from users.tests.helpers import bearer

API = "/api/v1"
DEBTS = f"{API}/debts/"


def fresh_key() -> str:
    return f'"{uuid.uuid4()}"'


def debt_url(debt_id: Any) -> str:
    return f"{DEBTS}{debt_id}/"


def repayments_url(debt_id: Any) -> str:
    return f"{DEBTS}{debt_id}/repayments/"


def repayment_url(debt_id: Any, repayment_id: Any) -> str:
    return f"{DEBTS}{debt_id}/repayments/{repayment_id}/"


def debt_body(**overrides: Any) -> dict[str, Any]:
    """The request of a correct debt; a test changes one field through ``overrides``."""
    body: dict[str, Any] = {
        "direction": "lent",
        "person": "Alex",
        "amount": "200.00",
        "currency": "USD",
        "start_date": "2026-10-01",
        "due_date": "2026-11-01",
        "note": "Laptop repair",
    }
    return {**body, **overrides}


def repayment_body(**overrides: Any) -> dict[str, Any]:
    return {**{"amount": "50.00", "date": "2026-10-20"}, **overrides}


def post(
    client: APIClient,
    url: str,
    token: str,
    body: dict[str, Any],
    key: str | None = None,
) -> Response:
    return client.post(
        url, body, headers={**bearer(token), "Idempotency-Key": key or fresh_key()}
    )


def add_debt(client: APIClient, token: str, **overrides: Any) -> Response:
    return post(client, DEBTS, token, debt_body(**overrides))


def add_repayment(
    client: APIClient, token: str, debt_id: Any, /, **overrides: Any
) -> Response:
    return post(client, repayments_url(debt_id), token, repayment_body(**overrides))


def patch(
    client: APIClient, url: str, token: str, body: dict[str, Any], **headers: str
) -> Response:
    return client.patch(url, body, headers={**bearer(token), **headers})


def delete(client: APIClient, url: str, token: str, **headers: str) -> Response:
    return client.delete(url, headers={**bearer(token), **headers})


def get(client: APIClient, url: str, token: str, **params: Any) -> Response:
    return client.get(url, params, headers=bearer(token))
