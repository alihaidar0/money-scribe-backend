"""Set up a person's data from the ``inputs`` of a vector case, and read the reports back.

The salary month, the Settings and the deletion of a category go through the real API; transactions and debts are
written straight to the database, because only their stored figures matter to a report.
"""

from typing import Any

from rest_framework.response import Response
from rest_framework.test import APIClient

from debts.models import Debt, Repayment
from ledger.models import Account, Category, Transaction
from ledger.tests.helpers import add_category, category_url, delete
from salary.tests.helpers import add_item, put_month
from users.models import User
from users.tests.helpers import bearer

API = "/api/v1"
SETTINGS = f"{API}/settings/"


def overview_url(month: str) -> str:
    return f"{API}/overview/months/{month}/"


def month_report_url(month: str) -> str:
    return f"{API}/reports/months/{month}/"


DEBTS_REPORT = f"{API}/reports/debts/"


def get(client: APIClient, url: str, token: str, **params: Any) -> Response:
    return client.get(url, params, headers=bearer(token))


def set_up(
    client: APIClient, me: tuple[User, str], inputs: dict[str, Any]
) -> dict[str, str]:
    """Make everything a case asks for, in an order that always works (the month before its items).

    Returns the ids the server gave the custom categories, by name.
    """
    user, token = me
    month = inputs.get("month", "2026-10")
    if inputs.get("settings"):
        answer = client.patch(SETTINGS, inputs["settings"], headers=bearer(token))
        assert answer.status_code == 200, answer.json()

    saved = inputs.get("salary_month")
    if saved is not None:
        items = saved["fixed_items"]
        body = {
            **{k: v for k, v in saved.items() if k != "fixed_items"},
            "first_pay_day": 20,
            "second_pay_day": 5,
        }
        answer = put_month(client, token, month, body)
        assert answer.status_code in (200, 201), answer.json()
        for item in items:
            answer = add_item(client, token, month, item)
            assert answer.status_code == 201, answer.json()

    custom: dict[str, str] = {}
    for spec in inputs.get("custom_categories", []):
        answer = add_category(client, token, spec["name"], spec["icon"])
        assert answer.status_code == 201, answer.json()
        custom[spec["name"]] = answer.json()["id"]

    wallet = Account.objects.create(owner=user, name="Wallet", kind="cash")
    for row in inputs.get("transactions", []):
        key = row.get("category", "other")
        category = (
            Category.objects.get(pk=custom[key.removeprefix("custom:")])
            if key.startswith("custom:")
            else Category.objects.get(key=key, owner__isnull=True)
        )
        Transaction.objects.create(
            owner=user,
            kind=row["kind"],
            amount=minor(row["amount"], row["currency"]),
            currency=row["currency"],
            date=row["date"],
            category=category,
            account=wallet,
        )

    for name in inputs.get("deleted_categories", []):
        answer = delete(client, category_url(custom[name]), token)
        assert answer.status_code == 204, answer.content

    for row in inputs.get("debts", []):
        debt = Debt.objects.create(
            owner=user,
            direction=row["direction"],
            person=row.get("person", "Pat"),
            amount=minor(row["amount"], row["currency"]),
            currency=row["currency"],
            start_date=row.get("start_date", "2026-09-01"),
            due_date=row.get("due_date"),
        )
        for repayment in row.get("repayments", []):
            Repayment.objects.create(
                debt=debt,
                amount=minor(repayment["amount"], row["currency"]),
                date=repayment["date"],
            )
    return custom


def minor(amount: str, currency: str) -> int:
    """Minor units of a decimal string, from the digits of the currency (read from the text, not from a float)."""
    whole, _, fraction = amount.partition(".")
    return int(whole + fraction)


def resolve_ids(expected: Any, custom: dict[str, str]) -> Any:
    """Replace a ``custom:<name>`` category id of an expected answer with the id the server gave that category."""
    if isinstance(expected, dict):
        return {k: resolve_ids(v, custom) for k, v in expected.items()}
    if isinstance(expected, list):
        return [resolve_ids(v, custom) for v in expected]
    if isinstance(expected, str) and expected.startswith("custom:"):
        return custom[expected.removeprefix("custom:")]
    return expected
