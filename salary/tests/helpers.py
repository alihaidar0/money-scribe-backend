import uuid
from typing import Any

from rest_framework.response import Response
from rest_framework.test import APIClient

from users.tests.helpers import bearer

API = "/api/v1/salary"


def month_body(**changes: Any) -> dict[str, Any]:
    """The inputs of the month of the contract (case A of the shared vectors, two payments)."""
    body: dict[str, Any] = {
        "currency": "RUB",
        "base_salary": "100000.00",
        "food_per_day": "500.00",
        "working_days_in_month": 20,
        "working_hours_in_month": 160,
        "extra_hours_hundredths": 800,
        "tax_rate_basis_points": 1300,
        "payment_mode": "two_payments",
        "working_days_first_half": 10,
        "first_pay_day": 20,
        "second_pay_day": 5,
    }
    return {**body, **changes}


def fresh_key() -> str:
    return f'"{uuid.uuid4()}"'


def put_month(
    client: APIClient,
    token: str,
    month: str = "2026-10",
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    return client.put(
        f"{API}/months/{month}/",
        month_body() if body is None else body,
        headers={**bearer(token), **(headers or {})},
    )


def add_item(
    client: APIClient,
    token: str,
    month: str = "2026-10",
    body: dict[str, Any] | None = None,
    key: str | None = None,
) -> Response:
    item = (
        {"name": "Rent", "amount": "3000.00", "kind": "expense"}
        if body is None
        else body
    )
    return client.post(
        f"{API}/months/{month}/fixed-items/",
        item,
        headers={**bearer(token), "Idempotency-Key": key or fresh_key()},
    )


def read_month(client: APIClient, token: str, month: str = "2026-10") -> Response:
    return client.get(f"{API}/months/{month}/", headers=bearer(token))


def item_url(item_id: str) -> str:
    return f"{API}/fixed-items/{item_id}/"
