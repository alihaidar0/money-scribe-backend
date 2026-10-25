"""Repayments: add, change, delete; every call answers the whole debt (ADR 0016; ``docs/api-fields.md``)."""

from datetime import date
from typing import Any

import pytest
from rest_framework.response import Response
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from debts.models import MAX_REPAYMENTS, Debt, Repayment
from debts.tests.factories import DebtFactory, RepaymentFactory
from debts.tests.helpers import (
    add_repayment,
    debt_url,
    delete,
    fresh_key,
    get,
    patch,
    post,
    repayment_body,
    repayment_url,
    repayments_url,
)
from users.models import User
from users.tests.helpers import first_error

pytestmark = pytest.mark.django_db

SOME_ID = "0198f2b0-4e5a-7d12-a3c4-6b7e8f9a0c1d"


@pytest.fixture(autouse=True)
def _today(clock: Any) -> None:
    """Every test runs on 2026-10-15."""


@pytest.fixture
def debt(me: tuple[User, str]) -> Debt:
    """200.00 USD lent, starting 2026-10-01, due 2026-11-01."""
    return DebtFactory.create(owner=me[0])


def errors_of(response: Response) -> list[tuple[str, Any]]:
    return [(e["code"], e["attr"]) for e in response.json()["errors"]]


# --- adding -----------------------------------------------------------------------------------


def test_a_repayment_answers_the_whole_debt_with_the_debts_own_etag(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    response = add_repayment(client, me[1], debt.id)

    assert response.status_code == 201
    assert response["ETag"] == '"1"'
    body = response.json()
    assert body["id"] == str(debt.id)
    assert (body["repaid"], body["remaining"], body["status"], body["version"]) == (
        "50.00",
        "150.00",
        "open",
        1,
    )
    [repayment] = body["repayments"]
    assert (repayment["amount"], repayment["date"], repayment["version"]) == (
        "50.00",
        "2026-10-20",
        1,
    )
    assert repayment["created_at"].endswith("Z")
    stored = Repayment.objects.get(pk=repayment["id"])
    assert (stored.debt, stored.amount) == (debt, 5000)


def test_a_repayment_does_not_change_the_version_of_the_debt(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    add_repayment(client, me[1], debt.id)
    add_repayment(client, me[1], debt.id, amount="10.00")

    debt.refresh_from_db()
    assert debt.version == 1
    assert get(client, debt_url(debt.id), me[1])["ETag"] == '"1"'


def test_the_last_repayment_settles_the_debt(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    add_repayment(client, me[1], debt.id, amount="150.00")

    response = add_repayment(client, me[1], debt.id, amount="50.00")

    body = response.json()
    assert response.status_code == 201
    assert (body["status"], body["remaining"], body["repaid"]) == (
        "settled",
        "0.00",
        "200.00",
    )


def test_the_repayments_of_the_answer_are_ordered_by_date(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    add_repayment(client, me[1], debt.id, date="2026-10-25", amount="10.00")

    response = add_repayment(client, me[1], debt.id, date="2026-10-05", amount="20.00")

    assert [r["date"] for r in response.json()["repayments"]] == [
        "2026-10-05",
        "2026-10-25",
    ]


def test_a_repeated_create_returns_the_first_answer_and_adds_nothing(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    key = fresh_key()

    first = post(client, repayments_url(debt.id), me[1], repayment_body(), key)
    again = post(client, repayments_url(debt.id), me[1], repayment_body(), key)

    assert (first.status_code, again.status_code) == (201, 201)
    assert again["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert again["ETag"] == '"1"'
    assert Repayment.objects.count() == 1


def test_the_same_key_for_another_debt_is_a_different_request(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    second = DebtFactory.create(owner=me[0])
    key = fresh_key()
    post(client, repayments_url(debt.id), me[1], repayment_body(), key)

    response = post(client, repayments_url(second.id), me[1], repayment_body(), key)

    assert response.status_code == 422
    assert Repayment.objects.count() == 1


def test_a_create_needs_an_idempotency_key(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    response = client.post(
        repayments_url(debt.id),
        repayment_body(),
        headers={"Authorization": f"Bearer {me[1]}"},
    )

    assert response.status_code == 400
    assert errors_of(response) == [("idempotency_key_required", "idempotency_key")]


def test_a_repayment_above_what_remains_is_refused_with_what_remains(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    RepaymentFactory.create(debt=debt, amount=5000)

    response = add_repayment(client, me[1], debt.id, amount="150.01")

    assert response.status_code == 400
    assert response.json() == {
        "type": "validation_error",
        "errors": [
            {
                "code": "exceeds_remaining",
                "detail": "The repayment is more than what remains.",
                "attr": "amount",
                "remaining": "150.00",
            }
        ],
    }
    assert Repayment.objects.count() == 1


def test_exactly_what_remains_is_accepted(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    RepaymentFactory.create(debt=debt, amount=5000)

    assert add_repayment(client, me[1], debt.id, amount="150.00").status_code == 201


def test_nothing_can_be_repaid_on_a_settled_debt(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    RepaymentFactory.create(debt=debt, amount=20000)

    response = add_repayment(client, me[1], debt.id, amount="0.01")

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["remaining"]) == ("exceeds_remaining", "0.00")


def test_remaining_is_written_with_the_digits_of_the_currency(
    client: APIClient, me: tuple[User, str]
) -> None:
    jpy = DebtFactory.create(owner=me[0], currency="JPY", amount=1200)
    kwd = DebtFactory.create(owner=me[0], currency="KWD", amount=12500)

    in_yen = add_repayment(client, me[1], jpy.id, amount="1201")
    in_dinar = add_repayment(client, me[1], kwd.id, amount="12.501")

    assert first_error(in_yen)["remaining"] == "1200"
    assert first_error(in_dinar)["remaining"] == "12.500"


def test_the_amount_is_read_in_the_currency_of_the_debt(
    client: APIClient, me: tuple[User, str]
) -> None:
    jpy = DebtFactory.create(owner=me[0], currency="JPY", amount=1200)

    wrong = add_repayment(client, me[1], jpy.id, amount="500.00")
    right = add_repayment(client, me[1], jpy.id, amount="500")

    assert errors_of(wrong) == [("invalid_amount", "amount")]
    assert right.status_code == 201
    assert right.json()["repaid"] == "500"


def test_a_currency_in_the_request_is_400_read_only_even_the_debts_own(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    same = add_repayment(client, me[1], debt.id, currency="USD")
    other = add_repayment(client, me[1], debt.id, currency="EUR")

    assert errors_of(same) == [("read_only", "currency")]
    assert errors_of(other) == [("read_only", "currency")]
    assert Repayment.objects.count() == 0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("amount", "0.00", "invalid_amount"),
        ("amount", "-1.00", "invalid_amount"),
        ("amount", "5", "invalid_amount"),
        ("amount", 50, "invalid_amount"),
        ("amount", None, "null"),
        ("date", "2026-13-01", "invalid"),
        ("date", "1999-12-31", "date_out_of_range"),
        ("date", "2100-01-01", "date_out_of_range"),
        ("date", None, "null"),
    ],
)
def test_a_bad_field_is_one_error_on_that_field(
    client: APIClient,
    me: tuple[User, str],
    debt: Debt,
    field: str,
    value: Any,
    code: str,
) -> None:
    response = add_repayment(client, me[1], debt.id, **{field: value})

    assert response.status_code == 400
    assert errors_of(response) == [(code, field)]


@pytest.mark.parametrize("field", ["amount", "date"])
def test_a_missing_field_is_required(
    client: APIClient, me: tuple[User, str], debt: Debt, field: str
) -> None:
    body = repayment_body()
    del body[field]

    response = post(client, repayments_url(debt.id), me[1], body)

    assert errors_of(response) == [("required", field)]


@pytest.mark.parametrize(
    "field", ["id", "version", "created_at", "debt", "debt_id", "unknown"]
)
def test_a_field_that_cannot_be_written_is_400_read_only(
    client: APIClient, me: tuple[User, str], debt: Debt, field: str
) -> None:
    response = add_repayment(client, me[1], debt.id, **{field: "x"})

    assert errors_of(response) == [("read_only", field)]


def test_a_repayment_before_the_debt_starts_is_refused_on_the_date(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    before = add_repayment(client, me[1], debt.id, date="2026-09-30")
    on = add_repayment(client, me[1], debt.id, date="2026-10-01")
    after_due = add_repayment(client, me[1], debt.id, date="2026-12-31")

    assert errors_of(before) == [("repayment_before_start", "date")]
    assert (on.status_code, after_due.status_code) == (201, 201)


def test_a_repayment_may_be_dated_in_the_future(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    assert add_repayment(client, me[1], debt.id, date="2027-06-01").status_code == 201


def test_the_limit_is_200_repayments_per_debt() -> None:
    assert MAX_REPAYMENTS == 200


def test_the_next_repayment_over_the_limit_is_409_and_the_limit_is_per_debt(
    client: APIClient,
    me: tuple[User, str],
    debt: Debt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("debts.services.MAX_REPAYMENTS", 3)
    RepaymentFactory.create_batch(3, debt=debt, amount=100)
    another = DebtFactory.create(owner=me[0])

    over = add_repayment(client, me[1], debt.id, amount="1.00")
    fine = add_repayment(client, me[1], another.id, amount="1.00")

    assert over.status_code == 409
    error = first_error(over)
    assert (error["code"], error["attr"]) == ("repayment_limit_reached", None)
    assert Repayment.objects.filter(debt=debt).count() == 3
    assert fine.status_code == 201


# --- changing ---------------------------------------------------------------------------------


def test_a_change_answers_the_whole_debt_and_keeps_the_debts_etag(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt, amount=5000)

    response = patch(
        client,
        repayment_url(debt.id, repayment.id),
        me[1],
        {"amount": "60.00"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response["ETag"] == '"1"'  # the debt's version
    body = response.json()
    assert (body["repaid"], body["remaining"]) == ("60.00", "140.00")
    assert body["repayments"][0]["version"] == 2  # the repayment's own
    repayment.refresh_from_db()
    assert (repayment.amount, repayment.version) == (6000, 2)
    debt.refresh_from_db()
    assert debt.version == 1


def test_the_date_can_be_changed_alone(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt)

    response = patch(
        client, repayment_url(debt.id, repayment.id), me[1], {"date": "2026-10-02"}
    )

    assert response.status_code == 200
    repayment.refresh_from_db()
    assert (repayment.date, repayment.amount) == (date(2026, 10, 2), 5000)


def test_a_repayments_own_old_amount_does_not_count_when_it_is_changed(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    mine = RepaymentFactory.create(debt=debt, amount=15000)
    RepaymentFactory.create(debt=debt, amount=2000)  # 30.00 remain besides mine...

    most = patch(client, repayment_url(debt.id, mine.id), me[1], {"amount": "180.00"})
    over = patch(client, repayment_url(debt.id, mine.id), me[1], {"amount": "180.01"})

    assert most.status_code == 200
    assert over.status_code == 400
    error = first_error(over)
    assert (error["code"], error["attr"], error["remaining"]) == (
        "exceeds_remaining",
        "amount",
        "180.00",
    )


def test_a_change_is_checked_against_the_start_date(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt)

    response = patch(
        client, repayment_url(debt.id, repayment.id), me[1], {"date": "2026-09-30"}
    )

    assert errors_of(response) == [("repayment_before_start", "date")]


def test_the_amount_of_a_change_is_read_in_the_debts_currency(
    client: APIClient, me: tuple[User, str]
) -> None:
    jpy = DebtFactory.create(owner=me[0], currency="JPY", amount=1200)
    repayment = RepaymentFactory.create(debt=jpy, amount=100)

    wrong = patch(
        client, repayment_url(jpy.id, repayment.id), me[1], {"amount": "200.00"}
    )
    right = patch(client, repayment_url(jpy.id, repayment.id), me[1], {"amount": "200"})

    assert errors_of(wrong) == [("invalid_amount", "amount")]
    assert right.json()["repaid"] == "200"


@pytest.mark.parametrize(
    ("field", "value"),
    [("currency", "USD"), ("currency", "EUR"), ("id", SOME_ID), ("version", 3)],
)
def test_a_field_that_cannot_be_changed_is_400_read_only(
    client: APIClient, me: tuple[User, str], debt: Debt, field: str, value: Any
) -> None:
    repayment = RepaymentFactory.create(debt=debt)

    response = patch(
        client, repayment_url(debt.id, repayment.id), me[1], {field: value}
    )

    assert errors_of(response) == [("read_only", field)]


def test_a_change_that_changes_nothing_keeps_the_repayments_version(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt)

    response = patch(
        client,
        repayment_url(debt.id, repayment.id),
        me[1],
        {"amount": "50.00", "date": "2026-10-20"},
    )

    assert response.status_code == 200
    assert response.json()["repayments"][0]["version"] == 1


def test_a_stale_change_is_412_and_changes_nothing(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt)
    patch(client, repayment_url(debt.id, repayment.id), me[1], {"amount": "60.00"})

    response = patch(
        client,
        repayment_url(debt.id, repayment.id),
        me[1],
        {"amount": "70.00"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 412
    assert first_error(response)["code"] == "precondition_failed"
    repayment.refresh_from_db()
    assert (repayment.amount, repayment.version) == (6000, 2)


def test_the_precondition_is_checked_before_the_body(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt)

    response = patch(
        client,
        repayment_url(debt.id, repayment.id),
        me[1],
        {"amount": "oops"},
        **{"If-Match": '"9"'},
    )

    assert response.status_code == 412


def test_the_if_match_of_a_repayment_is_its_own_version_not_the_debts(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt)
    debt.person = "Sam"
    debt.save()  # the debt is at version 2, the repayment still at 1

    response = patch(
        client,
        repayment_url(debt.id, repayment.id),
        me[1],
        {"amount": "60.00"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response["ETag"] == '"2"'


# --- deleting ---------------------------------------------------------------------------------


def test_a_delete_answers_the_whole_debt_and_a_repeat_is_404(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    gone = RepaymentFactory.create(debt=debt, amount=5000)
    kept = RepaymentFactory.create(debt=debt, amount=2000)

    first = delete(
        client, repayment_url(debt.id, gone.id), me[1], **{"If-Match": '"1"'}
    )
    again = delete(client, repayment_url(debt.id, gone.id), me[1])

    assert first.status_code == 200
    assert first["ETag"] == '"1"'
    body = first.json()
    assert [r["id"] for r in body["repayments"]] == [str(kept.id)]
    assert (body["repaid"], body["remaining"]) == ("20.00", "180.00")
    assert again.status_code == 404
    assert list(Repayment.objects.all()) == [kept]


def test_deleting_the_last_repayment_reopens_a_settled_debt(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    only = RepaymentFactory.create(debt=debt, amount=20000)

    response = delete(client, repayment_url(debt.id, only.id), me[1])

    body = response.json()
    assert (body["status"], body["repayments"]) == ("open", [])


def test_a_stale_delete_is_412_and_deletes_nothing(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt)

    response = delete(
        client, repayment_url(debt.id, repayment.id), me[1], **{"If-Match": '"4"'}
    )

    assert response.status_code == 412
    assert Repayment.objects.count() == 1


# --- who may touch them -----------------------------------------------------------------------


def test_a_repayment_under_another_debt_of_the_person_is_404(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    elsewhere = DebtFactory.create(owner=me[0])
    repayment = RepaymentFactory.create(debt=elsewhere)

    patched = patch(
        client, repayment_url(debt.id, repayment.id), me[1], {"amount": "1.00"}
    )
    deleted = delete(client, repayment_url(debt.id, repayment.id), me[1])

    assert (patched.status_code, deleted.status_code) == (404, 404)
    repayment.refresh_from_db()
    assert (repayment.amount, repayment.version) == (5000, 1)


def test_a_missing_debt_or_repayment_is_404(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    assert add_repayment(client, me[1], SOME_ID).status_code == 404
    assert (
        patch(
            client, repayment_url(debt.id, SOME_ID), me[1], {"amount": "1.00"}
        ).status_code
        == 404
    )
    assert delete(client, repayment_url(SOME_ID, SOME_ID), me[1]).status_code == 404


def test_another_persons_debt_and_repayment_are_404_for_every_method(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    theirs = DebtFactory.create(owner=other[0])
    repayment = RepaymentFactory.create(debt=theirs)
    mine_only = APIClient()
    mine_only.raise_request_exception = False
    mine_only.credentials(HTTP_AUTHORIZATION=f"Bearer {me[1]}")

    assert_hidden_from_other_users(
        mine_only,
        repayment_url(theirs.id, repayment.id),
        methods=("patch", "delete"),
    )
    assert (
        post(client, repayments_url(theirs.id), me[1], repayment_body()).status_code
        == 404
    )

    repayment.refresh_from_db()
    assert (repayment.amount, repayment.version) == (5000, 1)
    assert Repayment.objects.count() == 1


def test_every_operation_needs_a_signed_in_person(client: APIClient) -> None:
    assert client.post(repayments_url(SOME_ID), {}).status_code == 401
    assert client.patch(repayment_url(SOME_ID, SOME_ID), {}).status_code == 401
    assert client.delete(repayment_url(SOME_ID, SOME_ID)).status_code == 401


def test_a_repayment_has_no_read_put_or_list_of_its_own(
    client: APIClient, me: tuple[User, str], debt: Debt
) -> None:
    repayment = RepaymentFactory.create(debt=debt)
    headers = {"Authorization": f"Bearer {me[1]}"}

    assert client.get(repayments_url(debt.id), headers=headers).status_code == 405
    assert (
        client.get(repayment_url(debt.id, repayment.id), headers=headers).status_code
        == 405
    )
    assert (
        client.put(
            repayment_url(debt.id, repayment.id), repayment_body(), headers=headers
        ).status_code
        == 405
    )
