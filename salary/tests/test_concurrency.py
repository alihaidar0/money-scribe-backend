"""Two requests at once on the same month: no month saved twice, no item past the limit (real threads)."""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

import pytest
from django.db import connections
from rest_framework.test import APIClient

from salary import services
from salary.models import MAX_FIXED_ITEMS, FixedItem, SalaryMonth
from salary.tests.helpers import API, fresh_key, item_url, month_body
from users.tests.helpers import bearer, start_session

pytestmark = pytest.mark.django_db(transaction=True)


class Answer(NamedTuple):
    status: int
    body: Any


def call(
    method: str, url: str, token: str, body: dict[str, Any], **headers: str
) -> Answer:
    """One request from its own client and database connection, as a second device would make it."""
    client = APIClient()
    client.raise_request_exception = False
    try:
        response = getattr(client, method)(
            url, body, headers={**bearer(token), **headers}
        )
        return Answer(
            response.status_code, response.json() if response.content else None
        )
    finally:
        connections.close_all()


@pytest.fixture
def token() -> str:
    _, body = start_session(APIClient())
    return str(body["access_token"])


def slowed(monkeypatch: pytest.MonkeyPatch, name: str, seconds: float = 0.5) -> None:
    """Make a service step take a moment, so two requests really overlap inside it."""
    original: Callable[..., Any] = getattr(services, name)

    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(services, name, slow)


def together(first: Callable[[], Answer], second: Callable[[], Answer]) -> list[Answer]:
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(first)
        time.sleep(0.15)
        b = pool.submit(second)
        return [a.result(), b.result()]


def test_two_saves_of_a_new_month_at_once_make_one_month_and_both_succeed(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    slowed(monkeypatch, "save_month")
    url = f"{API}/months/2026-10/"

    answers = together(
        lambda: call("put", url, token, month_body(base_salary="1.00")),
        lambda: call("put", url, token, month_body(base_salary="2.00")),
    )

    assert sorted(a.status for a in answers) == [200, 201]
    assert SalaryMonth.objects.count() == 1
    assert SalaryMonth.objects.get().version == 2  # both counted


def test_two_creates_that_say_only_if_new_have_one_winner_and_one_412(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    slowed(monkeypatch, "save_month")
    url = f"{API}/months/2026-10/"
    only_new = {"If-None-Match": "*"}

    answers = together(
        lambda: call("put", url, token, month_body(base_salary="1.00"), **only_new),
        lambda: call("put", url, token, month_body(base_salary="2.00"), **only_new),
    )

    assert sorted(a.status for a in answers) == [201, 412]
    assert SalaryMonth.objects.count() == 1
    assert SalaryMonth.objects.get().base_salary == 100  # the first one stands


def test_two_edits_of_a_month_from_the_same_version_have_one_winner(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"{API}/months/2026-10/"
    call("put", url, token, month_body())
    slowed(monkeypatch, "save_month")
    from_one = {"If-Match": '"1"'}

    answers = together(
        lambda: call("put", url, token, month_body(base_salary="1.00"), **from_one),
        lambda: call("put", url, token, month_body(base_salary="2.00"), **from_one),
    )

    assert sorted(a.status for a in answers) == [200, 412]
    assert SalaryMonth.objects.get().base_salary == 100
    assert SalaryMonth.objects.get().version == 2


def test_two_adds_at_the_limit_have_one_winner_and_one_409(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    call("put", f"{API}/months/2026-10/", token, month_body())
    month = SalaryMonth.objects.get()
    FixedItem.objects.bulk_create(
        FixedItem(salary_month=month, name=f"n{n}", amount=1, kind="income")
        for n in range(MAX_FIXED_ITEMS - 1)
    )
    slowed(monkeypatch, "add_item")
    url = f"{API}/months/2026-10/fixed-items/"
    body = {"name": "Rent", "amount": "1.00", "kind": "expense"}

    answers = together(
        lambda: call("post", url, token, body, **{"Idempotency-Key": fresh_key()}),
        lambda: call("post", url, token, body, **{"Idempotency-Key": fresh_key()}),
    )

    assert sorted(a.status for a in answers) == [201, 409]
    assert FixedItem.objects.count() == MAX_FIXED_ITEMS


def test_two_changes_of_one_item_from_the_same_version_have_one_winner(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    call("put", f"{API}/months/2026-10/", token, month_body())
    created = call(
        "post",
        f"{API}/months/2026-10/fixed-items/",
        token,
        {"name": "Rent", "amount": "10.00", "kind": "expense"},
        **{"Idempotency-Key": fresh_key()},
    )
    url = item_url(created.body["id"])
    slowed(monkeypatch, "update_item")
    from_one = {"If-Match": '"1"'}

    answers = together(
        lambda: call("patch", url, token, {"amount": "11.00"}, **from_one),
        lambda: call("patch", url, token, {"amount": "12.00"}, **from_one),
    )

    assert sorted(a.status for a in answers) == [200, 412]
    assert FixedItem.objects.get().amount == 1100


def test_a_currency_change_and_an_add_never_leave_an_item_in_the_wrong_currency(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"{API}/months/2026-10/"
    call("put", url, token, month_body())
    slowed(monkeypatch, "add_item")

    answers = together(
        lambda: call(
            "post",
            f"{url}fixed-items/",
            token,
            {"name": "Rent", "amount": "10.00", "kind": "expense"},
            **{"Idempotency-Key": fresh_key()},
        ),
        lambda: call("put", url, token, month_body(currency="USD")),
    )

    month = SalaryMonth.objects.get()
    items = FixedItem.objects.count()
    assert answers[0].status == 201
    # the add held the month first, so the change of currency saw an item and was refused
    assert (answers[1].status, items, month.currency) == (409, 1, "RUB")
