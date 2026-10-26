"""The account and category lists run the same number of queries whatever they hold: no query per row (N+1).

The transaction list has its own test (test_transaction_list.py).
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from ledger.models import Account, Category
from ledger.tests.helpers import ACCOUNTS, CATEGORIES
from users.models import User
from users.tests.helpers import bearer

pytestmark = pytest.mark.django_db


def queries(client: APIClient, url: str, token: str) -> int:
    with CaptureQueriesContext(connection) as captured:
        response = client.get(url, headers=bearer(token))
    assert response.status_code == 200, response.json()
    return len(captured.captured_queries)


def add_rows(user: User, start: int, stop: int) -> None:
    """Accounts and custom categories numbered ``start`` to ``stop - 1``."""
    for n in range(start, stop):
        Account.objects.create(owner=user, name=f"Card {n}", kind="card")
        Category.objects.create(
            owner=user, name=f"Custom {n}", icon="gifts", kind="both"
        )


@pytest.mark.parametrize("url", [ACCOUNTS, CATEGORIES])
def test_a_list_does_not_query_per_row(
    client: APIClient, me: tuple[User, str], url: str
) -> None:
    user, token = me
    add_rows(user, 0, 1)
    few = queries(client, url, token)

    add_rows(user, 1, 10)
    many = queries(client, url, token)

    assert many == few
