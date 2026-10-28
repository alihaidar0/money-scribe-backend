"""Debts: add, read, change, delete (ADR 0008, 0016; ``docs/api-fields.md``)."""

from datetime import date
from typing import Any

import pytest
from rest_framework.response import Response
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from debts.models import MAX_DEBTS, Debt, Repayment
from debts.tests.factories import DebtFactory, RepaymentFactory
from debts.tests.helpers import (
    DEBTS,
    add_debt,
    debt_body,
    debt_url,
    delete,
    fresh_key,
    get,
    patch,
    post,
)
from idempotency.models import StoredResponse
from users.models import User
from users.tests.helpers import first_error

pytestmark = pytest.mark.django_db

SOME_ID = "0198f2b0-4e5a-7d12-a3c4-6b7e8f9a0c1d"


@pytest.fixture(autouse=True)
def _today(clock: Any) -> None:
    """Every test runs on 2026-10-15, so a due day is overdue or not the same way every time."""


def mine(me: tuple[User, str], **fields: Any) -> Debt:
    return DebtFactory.create(owner=me[0], **fields)


def only_error(response: Response) -> tuple[str, Any, int]:
    """The one error of a ``400``: its code and field, and how many errors there are."""
    errors = response.json()["errors"]
    return errors[0]["code"], errors[0]["attr"], len(errors)


# --- adding -----------------------------------------------------------------------------------


def test_a_debt_is_added_with_its_derived_values_and_etag(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_debt(client, me[1])

    assert response.status_code == 201
    body = response.json()
    assert response["ETag"] == '"1"'
    assert {
        k: v for k, v in body.items() if k not in ("id", "created_at", "updated_at")
    } == {
        "direction": "lent",
        "person": "Alex",
        "amount": "200.00",
        "currency": "USD",
        "start_date": "2026-10-01",
        "due_date": "2026-11-01",
        "note": "Laptop repair",
        "repaid": "0.00",
        "remaining": "200.00",
        "status": "open",
        "repayments": [],
        "version": 1,
    }
    debt = Debt.objects.get(pk=body["id"])
    assert (debt.owner, debt.amount, debt.currency) == (me[0], 20000, "USD")
    assert body["created_at"].endswith("Z") and body["updated_at"].endswith("Z")


def test_the_due_day_and_the_note_are_optional(
    client: APIClient, me: tuple[User, str]
) -> None:
    body = debt_body()
    del body["due_date"], body["note"]

    response = post(client, DEBTS, me[1], body)

    assert response.status_code == 201
    assert (response.json()["due_date"], response.json()["note"]) == (None, "")


def test_the_due_day_may_be_null_on_a_create(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_debt(client, me[1], due_date=None)

    assert response.status_code == 201
    assert response.json()["due_date"] is None


def test_a_borrowed_debt_in_another_currency_keeps_that_currency_digits(
    client: APIClient, me: tuple[User, str]
) -> None:
    kwd = add_debt(client, me[1], direction="borrowed", currency="KWD", amount="12.500")
    jpy = add_debt(client, me[1], currency="JPY", amount="1200")

    assert (kwd.json()["direction"], kwd.json()["amount"]) == ("borrowed", "12.500")
    assert (jpy.json()["amount"], jpy.json()["remaining"]) == ("1200", "1200")
    assert Debt.objects.get(pk=kwd.json()["id"]).amount == 12500


def test_text_is_trimmed_and_normalized(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_debt(client, me[1], person="  Alex  ", note="  Café  ")

    assert response.status_code == 201
    assert (response.json()["person"], response.json()["note"]) == ("Alex", "Café")


def test_a_create_needs_an_idempotency_key(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = client.post(
        DEBTS, debt_body(), headers={"Authorization": f"Bearer {me[1]}"}
    )

    assert response.status_code == 400
    assert only_error(response)[:2] == ("idempotency_key_required", "idempotency_key")


def test_a_repeated_create_returns_the_first_answer_and_adds_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    key = fresh_key()

    first = post(client, DEBTS, me[1], debt_body(), key)
    again = post(client, DEBTS, me[1], debt_body(), key)

    assert (first.status_code, again.status_code) == (201, 201)
    assert again["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert again["ETag"] == '"1"'
    assert Debt.objects.count() == 1


def test_the_same_key_with_another_body_is_422(
    client: APIClient, me: tuple[User, str]
) -> None:
    key = fresh_key()
    post(client, DEBTS, me[1], debt_body(), key)

    response = post(client, DEBTS, me[1], debt_body(person="Sam"), key)

    assert response.status_code == 422
    assert first_error(response)["code"] == "idempotency_key_reused"
    assert Debt.objects.count() == 1


def test_a_rejected_create_keeps_no_key(
    client: APIClient, me: tuple[User, str]
) -> None:
    key = fresh_key()

    assert post(client, DEBTS, me[1], debt_body(amount="0.00"), key).status_code == 400
    assert StoredResponse.objects.count() == 0
    assert post(client, DEBTS, me[1], debt_body(), key).status_code == 201


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("person", "", "blank"),
        ("person", "   ", "blank"),
        ("person", "x" * 51, "max_length"),
        ("person", "Al\u0000ex", "invalid_characters"),
        ("person", "Al‮ex", "invalid_characters"),
        ("person", 12, "invalid"),
        ("person", None, "null"),
        ("note", "x" * 101, "max_length"),
        ("note", "line\nbreak", "invalid_characters"),
        ("note", None, "null"),
        ("direction", "gave", "invalid_choice"),
        ("direction", None, "null"),
        ("currency", "XXX", "invalid_choice"),
        ("currency", None, "null"),
        ("amount", "0.00", "invalid_amount"),
        ("amount", "-5.00", "invalid_amount"),
        ("amount", "5", "invalid_amount"),
        ("amount", "5.000", "invalid_amount"),
        ("amount", "1,000.00", "invalid_amount"),
        ("amount", 200, "invalid_amount"),
        ("amount", "10000000000000.00", "invalid_amount"),
        ("amount", None, "null"),
        ("start_date", "2026-13-01", "invalid"),
        ("start_date", "01/10/2026", "invalid"),
        ("start_date", 20261001, "invalid"),
        ("start_date", "1999-12-31", "date_out_of_range"),
        ("start_date", "2100-01-01", "date_out_of_range"),
        ("start_date", None, "null"),
        ("due_date", "2026-02-30", "invalid"),
        ("due_date", "2100-01-01", "date_out_of_range"),
    ],
)
def test_a_bad_field_is_one_error_on_that_field(
    client: APIClient, me: tuple[User, str], field: str, value: Any, code: str
) -> None:
    response = add_debt(client, me[1], **{field: value})

    assert response.status_code == 400
    assert response.json()["type"] == "validation_error"
    assert only_error(response) == (code, field, 1)
    assert Debt.objects.count() == 0


@pytest.mark.parametrize(
    "field", ["direction", "person", "amount", "currency", "start_date"]
)
def test_a_missing_required_field_is_required(
    client: APIClient, me: tuple[User, str], field: str
) -> None:
    body = debt_body()
    del body[field]

    response = post(client, DEBTS, me[1], body)

    assert response.status_code == 400
    assert only_error(response) == ("required", field, 1)


def test_the_largest_amount_is_999_999_999_999_999_minor_units(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_debt(client, me[1], amount="9999999999999.99")

    assert response.status_code == 201
    assert Debt.objects.get().amount == 999_999_999_999_999


def test_due_before_start_is_an_error_on_the_due_date(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_debt(client, me[1], start_date="2026-10-10", due_date="2026-10-09")

    assert response.status_code == 400
    assert only_error(response) == ("due_before_start", "due_date", 1)
    assert Debt.objects.count() == 0


def test_the_due_day_may_be_the_start_day(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = add_debt(client, me[1], start_date="2026-10-10", due_date="2026-10-10")

    assert response.status_code == 201


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "repaid",
        "remaining",
        "status",
        "repayments",
        "version",
        "created_at",
        "updated_at",
        "owner",
        "is_settled",
        "unknown",
    ],
)
def test_a_field_that_cannot_be_written_is_400_read_only(
    client: APIClient, me: tuple[User, str], field: str
) -> None:
    response = add_debt(client, me[1], **{field: "x"})

    assert response.status_code == 400
    assert only_error(response) == ("read_only", field, 1)
    assert Debt.objects.count() == 0


def test_the_limit_is_500_debts_per_person() -> None:
    assert MAX_DEBTS == 500


def test_the_next_debt_over_the_limit_is_409_and_the_limit_is_per_person(
    client: APIClient,
    me: tuple[User, str],
    other: tuple[User, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("debts.services.MAX_DEBTS", 3)
    for _ in range(3):
        mine(me)

    over = add_debt(client, me[1])
    fine = add_debt(client, other[1])

    assert over.status_code == 409
    error = first_error(over)
    assert (error["code"], error["attr"]) == ("debt_limit_reached", None)
    assert Debt.objects.filter(owner=me[0]).count() == 3
    assert fine.status_code == 201


# --- reading ----------------------------------------------------------------------------------


def test_a_debt_is_read_with_its_etag_and_repayments(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)
    RepaymentFactory.create(debt=debt, amount=5000)

    response = get(client, debt_url(debt.id), me[1])

    assert response.status_code == 200
    assert response["ETag"] == '"1"'
    body = response.json()
    assert (body["repaid"], body["remaining"], body["status"]) == (
        "50.00",
        "150.00",
        "open",
    )
    assert [r["amount"] for r in body["repayments"]] == ["50.00"]
    assert set(body["repayments"][0]) == {
        "id",
        "amount",
        "date",
        "version",
        "created_at",
        "updated_at",
    }


def test_the_status_is_overdue_after_the_due_day_in_the_persons_own_day(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, due_date=date(2026, 10, 14))  # the frozen day is 2026-10-15

    response = get(client, debt_url(debt.id), me[1])

    assert response.json()["status"] == "overdue"


def test_the_due_day_itself_is_not_overdue(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, due_date=date(2026, 10, 15))

    assert get(client, debt_url(debt.id), me[1]).json()["status"] == "open"


def test_the_persons_time_zone_decides_the_day(
    client: APIClient, me: tuple[User, str], clock: Any
) -> None:
    from datetime import UTC, datetime

    clock.current = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)
    debt = mine(me, due_date=date(2026, 10, 14))
    assert get(client, debt_url(debt.id), me[1]).json()["status"] == "overdue"

    User.objects.filter(pk=me[0].pk).update(time_zone="Etc/GMT+12")  # still the 14th

    assert get(client, debt_url(debt.id), me[1]).json()["status"] == "open"


def test_a_read_takes_a_today_that_replaces_the_persons_day(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, due_date=date(2026, 11, 1))

    early = get(client, debt_url(debt.id), me[1], today="2026-11-01")
    late = get(client, debt_url(debt.id), me[1], today="2026-11-02")

    assert (early.json()["status"], late.json()["status"]) == ("open", "overdue")


@pytest.mark.parametrize(
    ("today", "code"),
    [
        ("tomorrow", "invalid"),
        ("2026-02-30", "invalid"),
        ("2100-01-01", "date_out_of_range"),
    ],
)
def test_a_bad_today_is_a_400_on_today(
    client: APIClient, me: tuple[User, str], today: str, code: str
) -> None:
    debt = mine(me)

    response = get(client, debt_url(debt.id), me[1], today=today)

    assert response.status_code == 400
    assert only_error(response) == (code, "today", 1)


def test_a_settled_debt_is_settled_even_when_it_is_past_due(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, due_date=date(2026, 10, 14))
    RepaymentFactory.create(debt=debt, amount=20000)

    body = get(client, debt_url(debt.id), me[1]).json()

    assert (body["status"], body["remaining"], body["repaid"]) == (
        "settled",
        "0.00",
        "200.00",
    )


# --- changing ---------------------------------------------------------------------------------


def test_a_change_raises_the_version_and_answers_the_new_etag(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)

    response = patch(
        client,
        debt_url(debt.id),
        me[1],
        {"due_date": "2026-11-15"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response["ETag"] == '"2"'
    body = response.json()
    assert (body["due_date"], body["version"], body["person"]) == (
        "2026-11-15",
        2,
        "Alex",
    )


def test_every_writable_field_can_be_changed(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)

    response = patch(
        client,
        debt_url(debt.id),
        me[1],
        {
            "direction": "borrowed",
            "person": "Sam",
            "amount": "250.00",
            "start_date": "2026-09-01",
            "due_date": "2026-12-01",
            "note": "New note",
        },
    )

    assert response.status_code == 200
    debt.refresh_from_db()
    assert (
        debt.direction,
        debt.person,
        debt.amount,
        debt.start_date,
        debt.due_date,
        debt.note,
    ) == (
        "borrowed",
        "Sam",
        25000,
        date(2026, 9, 1),
        date(2026, 12, 1),
        "New note",
    )
    assert response.json()["remaining"] == "250.00"


def test_null_clears_the_due_day_and_an_empty_string_the_note(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)

    response = patch(client, debt_url(debt.id), me[1], {"due_date": None, "note": ""})

    assert response.status_code == 200
    body = response.json()
    assert (body["due_date"], body["note"]) == (None, "")
    debt.refresh_from_db()
    assert (debt.due_date, debt.note) == (None, "")


def test_a_null_note_is_a_400(client: APIClient, me: tuple[User, str]) -> None:
    debt = mine(me)

    response = patch(client, debt_url(debt.id), me[1], {"note": None})

    assert response.status_code == 400
    assert only_error(response) == ("null", "note", 1)
    debt.refresh_from_db()
    assert (debt.note, debt.version) == ("Laptop repair", 1)


def test_fields_that_are_not_sent_keep_their_value(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)

    patch(client, debt_url(debt.id), me[1], {"person": "Sam"})

    debt.refresh_from_db()
    assert (debt.amount, debt.due_date, debt.note, debt.direction) == (
        20000,
        date(2026, 11, 1),
        "Laptop repair",
        "lent",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", "EUR"),
        ("currency", "USD"),  # even the stored value
        ("repaid", "10.00"),
        ("remaining", "0.00"),
        ("status", "settled"),
        ("repayments", []),
        ("id", SOME_ID),
        ("version", 9),
        ("created_at", "2026-01-01T00:00:00Z"),
        ("updated_at", "2026-01-01T00:00:00Z"),
        ("unknown", 1),
    ],
)
def test_a_field_that_cannot_be_changed_is_400_read_only(
    client: APIClient, me: tuple[User, str], field: str, value: Any
) -> None:
    debt = mine(me)

    response = patch(client, debt_url(debt.id), me[1], {field: value})

    assert response.status_code == 400
    assert only_error(response) == ("read_only", field, 1)
    debt.refresh_from_db()
    assert debt.version == 1


def test_the_amount_of_a_change_is_read_in_the_stored_currency(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, currency="JPY", amount=1200)

    wrong = patch(client, debt_url(debt.id), me[1], {"amount": "1300.00"})
    right = patch(client, debt_url(debt.id), me[1], {"amount": "1300"})

    assert only_error(wrong) == ("invalid_amount", "amount", 1)
    assert right.status_code == 200
    assert right.json()["amount"] == "1300"


def test_the_amount_cannot_go_below_what_was_repaid(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)
    RepaymentFactory.create(debt=debt, amount=5000)
    RepaymentFactory.create(debt=debt, amount=2500)

    below = patch(client, debt_url(debt.id), me[1], {"amount": "74.99"})
    equal = patch(client, debt_url(debt.id), me[1], {"amount": "75.00"})

    assert below.status_code == 400
    assert only_error(below) == ("amount_below_repaid", "amount", 1)
    assert equal.status_code == 200
    assert (equal.json()["status"], equal.json()["remaining"]) == ("settled", "0.00")


def test_raising_the_amount_of_a_settled_debt_reopens_it(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, amount=5000)
    RepaymentFactory.create(debt=debt, amount=5000)
    assert get(client, debt_url(debt.id), me[1]).json()["status"] == "settled"

    response = patch(client, debt_url(debt.id), me[1], {"amount": "80.00"})

    body = response.json()
    assert (body["status"], body["remaining"], body["repaid"]) == (
        "open",
        "30.00",
        "50.00",
    )


def test_a_change_of_the_due_day_checks_it_against_the_stored_start(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)

    response = patch(client, debt_url(debt.id), me[1], {"due_date": "2026-09-30"})

    assert response.status_code == 400
    assert only_error(response) == ("due_before_start", "due_date", 1)


def test_moving_the_start_past_the_stored_due_day_is_an_error_on_the_due_date(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)  # due 2026-11-01

    response = patch(client, debt_url(debt.id), me[1], {"start_date": "2026-11-02"})

    assert response.status_code == 400
    assert only_error(response) == ("due_before_start", "due_date", 1)


def test_the_start_and_the_due_day_can_move_together(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)

    response = patch(
        client,
        debt_url(debt.id),
        me[1],
        {"start_date": "2026-11-02", "due_date": "2026-12-02"},
    )

    assert response.status_code == 200


def test_the_start_cannot_move_past_a_repayment(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, due_date=None)
    RepaymentFactory.create(debt=debt, date=date(2026, 10, 20))

    past = patch(client, debt_url(debt.id), me[1], {"start_date": "2026-10-21"})
    onto = patch(client, debt_url(debt.id), me[1], {"start_date": "2026-10-20"})

    assert only_error(past) == ("start_after_repayment", "start_date", 1)
    assert onto.status_code == 200


def test_several_broken_rules_are_reported_together(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)
    RepaymentFactory.create(debt=debt, amount=5000, date=date(2026, 10, 20))

    response = patch(
        client,
        debt_url(debt.id),
        me[1],
        {"amount": "10.00", "start_date": "2026-10-25", "due_date": "2026-10-24"},
    )

    errors = {e["attr"]: e["code"] for e in response.json()["errors"]}
    assert errors == {
        "amount": "amount_below_repaid",
        "start_date": "start_after_repayment",
        "due_date": "due_before_start",
    }


def test_a_change_that_changes_nothing_keeps_the_version(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)

    response = patch(
        client,
        debt_url(debt.id),
        me[1],
        {"person": "Alex", "amount": "200.00", "due_date": "2026-11-01"},
    )

    assert response.status_code == 200
    assert (response["ETag"], response.json()["version"]) == ('"1"', 1)


def test_the_direction_can_be_flipped_and_everything_else_stays(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, due_date=date(2026, 10, 14))  # overdue on the frozen day
    repayment = RepaymentFactory.create(debt=debt, amount=5000)
    before = get(client, debt_url(debt.id), me[1]).json()

    response = patch(
        client,
        debt_url(debt.id),
        me[1],
        {"direction": "borrowed"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 200
    assert response["ETag"] == '"2"'
    body = response.json()
    assert (body["direction"], body["version"]) == ("borrowed", 2)
    changed = {"direction", "version", "updated_at"}
    assert {k: v for k, v in body.items() if k not in changed} == {
        k: v for k, v in before.items() if k not in changed
    }
    assert [item["id"] for item in body["repayments"]] == [str(repayment.id)]
    debt.refresh_from_db()
    assert (debt.direction, debt.version, debt.currency) == ("borrowed", 2, "USD")
    assert Repayment.objects.filter(debt=debt).count() == 1


def test_sending_the_stored_direction_saves_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, direction="lent")

    response = patch(client, debt_url(debt.id), me[1], {"direction": "lent"})

    assert response.status_code == 200
    debt.refresh_from_db()
    assert (debt.direction, debt.version) == ("lent", 1)


@pytest.mark.parametrize(
    ("value", "code"),
    [("gave", "invalid_choice"), (None, "null"), ("", "invalid_choice")],
)
def test_a_bad_direction_is_a_400_on_direction(
    client: APIClient, me: tuple[User, str], value: Any, code: str
) -> None:
    debt = mine(me)

    response = patch(client, debt_url(debt.id), me[1], {"direction": value})

    assert response.status_code == 400
    assert only_error(response) == (code, "direction", 1)
    debt.refresh_from_db()
    assert (debt.direction, debt.version) == ("lent", 1)


def test_a_stale_flip_is_412_and_changes_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)
    patch(client, debt_url(debt.id), me[1], {"person": "Sam"})  # now version 2

    response = patch(
        client,
        debt_url(debt.id),
        me[1],
        {"direction": "borrowed"},
        **{"If-Match": '"1"'},
    )

    assert response.status_code == 412
    debt.refresh_from_db()
    assert (debt.direction, debt.version) == ("lent", 2)


def test_the_list_filter_follows_a_flipped_direction(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me, direction="lent")
    patch(client, debt_url(debt.id), me[1], {"direction": "borrowed"})

    lent = get(client, DEBTS, me[1], direction="lent").json()["results"]
    borrowed = get(client, DEBTS, me[1], direction="borrowed").json()["results"]

    assert ([d["id"] for d in lent], [d["id"] for d in borrowed]) == (
        [],
        [str(debt.id)],
    )


def test_a_stale_change_is_412_and_changes_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)
    patch(client, debt_url(debt.id), me[1], {"person": "Sam"})  # now version 2

    response = patch(
        client, debt_url(debt.id), me[1], {"person": "Max"}, **{"If-Match": '"1"'}
    )

    assert response.status_code == 412
    assert first_error(response)["code"] == "precondition_failed"
    debt.refresh_from_db()
    assert (debt.person, debt.version) == ("Sam", 2)


def test_a_repayment_does_not_make_a_change_stale(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)
    RepaymentFactory.create(debt=debt)

    response = patch(
        client, debt_url(debt.id), me[1], {"person": "Sam"}, **{"If-Match": '"1"'}
    )

    assert response.status_code == 200


def test_put_is_not_offered(client: APIClient, me: tuple[User, str]) -> None:
    debt = mine(me)

    response = client.put(
        debt_url(debt.id), debt_body(), headers={"Authorization": f"Bearer {me[1]}"}
    )

    assert response.status_code == 405


# --- deleting ---------------------------------------------------------------------------------


def test_a_debt_is_deleted_with_its_repayments_and_a_repeat_is_404(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)
    RepaymentFactory.create(debt=debt)
    RepaymentFactory.create(debt=debt)
    keep = mine(me)

    first = delete(client, debt_url(debt.id), me[1], **{"If-Match": '"1"'})
    again = delete(client, debt_url(debt.id), me[1])

    assert (first.status_code, again.status_code) == (204, 404)
    assert list(Debt.objects.all()) == [keep]
    assert Repayment.objects.count() == 0


def test_a_stale_delete_is_412_and_deletes_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = mine(me)
    RepaymentFactory.create(debt=debt)

    response = delete(client, debt_url(debt.id), me[1], **{"If-Match": '"7"'})

    assert response.status_code == 412
    assert Debt.objects.count() == 1
    assert Repayment.objects.count() == 1


# --- who may see them -------------------------------------------------------------------------


def test_another_persons_debt_is_404_for_every_method(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    theirs = DebtFactory.create(owner=other[0])
    RepaymentFactory.create(debt=theirs)
    mine_only = APIClient()
    mine_only.raise_request_exception = False
    mine_only.credentials(HTTP_AUTHORIZATION=f"Bearer {me[1]}")

    assert_hidden_from_other_users(
        mine_only, debt_url(theirs.id), methods=("get", "patch", "delete")
    )

    theirs.refresh_from_db()
    assert (theirs.person, theirs.version) == ("Alex", 1)
    assert Repayment.objects.count() == 1


def test_every_operation_needs_a_signed_in_person(client: APIClient) -> None:
    assert client.get(DEBTS).status_code == 401
    assert client.post(DEBTS, {}).status_code == 401
    assert client.get(debt_url(SOME_ID)).status_code == 401
    assert client.patch(debt_url(SOME_ID), {}).status_code == 401
    assert client.delete(debt_url(SOME_ID)).status_code == 401
