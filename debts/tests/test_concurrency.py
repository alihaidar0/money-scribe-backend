"""Two requests at once on a debt: nothing is overpaid and no limit is passed (the row locks of ``debts.services``)."""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

import pytest
from django.db import connections
from rest_framework.test import APIClient

from debts import services
from debts.models import Debt, Repayment
from debts.tests.factories import DebtFactory, RepaymentFactory
from debts.tests.helpers import (
    DEBTS,
    debt_body,
    debt_url,
    fresh_key,
    repayment_body,
    repayment_url,
    repayments_url,
)
from users.models import User
from users.tests.helpers import bearer, start_session

pytestmark = pytest.mark.django_db(transaction=True)


class Answer(NamedTuple):
    status: int
    body: Any


def call(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
    **headers: str,
) -> Answer:
    """One request from its own client and database connection, as a second device would make it."""
    client = APIClient()
    client.raise_request_exception = False
    try:
        response = (
            getattr(client, method)(url, body, headers={**bearer(token), **headers})
            if body is not None
            else getattr(client, method)(url, headers={**bearer(token), **headers})
        )
        return Answer(
            response.status_code, response.json() if response.content else None
        )
    finally:
        connections.close_all()


def repay(debt: Debt, token: str, amount: str) -> Answer:
    return call(
        "post",
        repayments_url(debt.id),
        token,
        repayment_body(amount=amount),
        **{"Idempotency-Key": fresh_key()},
    )


@pytest.fixture
def me() -> tuple[User, str]:
    user, body = start_session(APIClient())
    return user, str(body["access_token"])


def slowed(monkeypatch: pytest.MonkeyPatch, name: str, seconds: float = 0.5) -> None:
    """Make a service step take a moment after it has read, so the two requests really overlap.

    The step does its work first and waits afterwards: what it read is stale by the time the caller acts on it,
    unless the row lock kept the other request out.
    """
    original: Callable[..., Any] = getattr(services, name)

    def slow(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        time.sleep(seconds)
        return result

    monkeypatch.setattr(services, name, slow)


def together(first: Callable[[], Answer], second: Callable[[], Answer]) -> list[Answer]:
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(first)
        time.sleep(0.15)
        b = pool.submit(second)
        return [a.result(), b.result()]


def test_two_repayments_that_together_overpay_have_one_winner(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    debt = DebtFactory.create(owner=me[0], amount=10000)  # 100.00
    slowed(monkeypatch, "repaid_of")

    answers = together(
        lambda: repay(debt, me[1], "70.00"), lambda: repay(debt, me[1], "70.00")
    )

    assert sorted(a.status for a in answers) == [201, 400]
    loser = next(a for a in answers if a.status == 400)
    error = loser.body["errors"][0]
    assert (error["code"], error["attr"], error["remaining"]) == (
        "exceeds_remaining",
        "amount",
        "30.00",
    )
    assert [r.amount for r in Repayment.objects.all()] == [7000]


def test_two_repayments_that_fit_together_both_succeed(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    debt = DebtFactory.create(owner=me[0], amount=10000)
    slowed(monkeypatch, "repaid_of")

    answers = together(
        lambda: repay(debt, me[1], "60.00"), lambda: repay(debt, me[1], "40.00")
    )

    assert [a.status for a in answers] == [201, 201]
    assert sum(r.amount for r in Repayment.objects.all()) == 10000
    debt.refresh_from_db()
    assert debt.version == 1  # neither device got a 412 from the other


def test_two_edits_that_together_overpay_have_one_winner(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    debt = DebtFactory.create(owner=me[0], amount=10000)
    first = RepaymentFactory.create(debt=debt, amount=1000)
    second = RepaymentFactory.create(debt=debt, amount=1000)
    slowed(monkeypatch, "repaid_of")

    def edit(repayment: Repayment) -> Answer:
        return call(
            "patch", repayment_url(debt.id, repayment.id), me[1], {"amount": "70.00"}
        )

    answers = together(lambda: edit(first), lambda: edit(second))

    assert sorted(a.status for a in answers) == [200, 400]
    assert sum(r.amount for r in Repayment.objects.all()) == 8000


def test_a_repayment_and_a_lowering_of_the_amount_cannot_both_win(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    debt = DebtFactory.create(owner=me[0], amount=20000)
    slowed(monkeypatch, "repaid_of")

    answers = together(
        lambda: repay(debt, me[1], "150.00"),
        lambda: call("patch", debt_url(debt.id), me[1], {"amount": "100.00"}),
    )

    assert sorted(a.status for a in answers) == [200, 400] or sorted(
        a.status for a in answers
    ) == [201, 400]
    debt.refresh_from_db()
    repaid = sum(r.amount for r in Repayment.objects.all())
    assert repaid <= debt.amount  # never overpaid, whichever request came first


def test_deleting_a_debt_while_a_repayment_is_added_leaves_nothing_behind(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    debt = DebtFactory.create(owner=me[0])
    slowed(monkeypatch, "repaid_of")

    answers = together(
        lambda: repay(debt, me[1], "10.00"),
        lambda: call("delete", debt_url(debt.id), me[1]),
    )

    assert answers[1].status == 204
    assert answers[0].status in (201, 404)
    assert Debt.objects.count() == 0
    assert Repayment.objects.count() == 0


def test_two_debts_at_the_limit_have_one_winner_and_one_409(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "MAX_DEBTS", 2)
    DebtFactory.create(owner=me[0])
    slowed(monkeypatch, "dates_error")

    def create() -> Answer:
        return call(
            "post",
            DEBTS,
            me[1],
            debt_body(),
            **{"Idempotency-Key": fresh_key()},
        )

    answers = together(create, create)

    assert sorted(a.status for a in answers) == [201, 409]
    assert Debt.objects.filter(owner=me[0]).count() == 2


def test_two_repayments_at_the_limit_have_one_winner_and_one_409(
    me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "MAX_REPAYMENTS", 2)
    debt = DebtFactory.create(owner=me[0])
    RepaymentFactory.create(debt=debt, amount=100)
    slowed(monkeypatch, "repaid_of")

    answers = together(
        lambda: repay(debt, me[1], "1.00"), lambda: repay(debt, me[1], "1.00")
    )

    assert sorted(a.status for a in answers) == [201, 409]
    assert Repayment.objects.filter(debt=debt).count() == 2
