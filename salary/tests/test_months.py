"""Saving, reading, listing and deleting a salary month (ADR 0007)."""

from typing import Any

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from salary.models import FixedItem, SalaryMonth
from salary.tests.helpers import API, add_item, month_body, put_month, read_month
from users.models import User
from users.tests.helpers import bearer, first_error, start_session

pytestmark = pytest.mark.django_db

CASE_A = {
    "results": {
        "food_allowance": "10000.00",
        "extra_hours_pay": "10000.00",
        "income_before_tax": "120000.00",
        "tax": "15600.00",
        "total_after_tax": "104400.00",
    },
    "payments": [
        {"part": "first", "amount": "43500.00"},
        {"part": "second", "amount": "60900.00"},
    ],
}


# --- saving ---------------------------------------------------------------------------------


def test_a_new_month_is_201_with_the_inputs_the_results_and_the_etag(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = put_month(client, account[1])

    body = response.json()
    assert response.status_code == 201
    assert response["ETag"] == '"1"'
    assert body["month"] == "2026-10"
    assert body["currency"] == "RUB"
    assert body["base_salary"] == "100000.00"
    assert body["food_per_day"] == "500.00"
    assert body["working_days_in_month"] == 20
    assert body["working_hours_in_month"] == 160
    assert body["extra_hours_hundredths"] == 800
    assert body["tax_rate_basis_points"] == 1300
    assert body["payment_mode"] == "two_payments"
    assert body["working_days_first_half"] == 10
    assert (body["first_pay_day"], body["second_pay_day"]) == (20, 5)
    assert body["calculation_version"] == 1
    assert body["results"] == CASE_A["results"]
    assert body["payments"] == CASE_A["payments"]
    assert body["fixed_items"] == []
    assert body["fixed_totals"] == {
        "first": {"income": "0.00", "expense": "0.00"},
        "second": {"income": "0.00", "expense": "0.00"},
    }
    assert body["balances"] == {
        "first": "43500.00",
        "second": "60900.00",
        "month": "104400.00",
    }
    assert body["version"] == 1
    assert body["id"]
    assert body["created_at"].endswith("Z")


def test_saving_again_replaces_the_inputs_and_recalculates(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])

    response = put_month(client, account[1], body=month_body(base_salary="200000.00"))

    body = response.json()
    assert response.status_code == 200
    assert response["ETag"] == '"2"'
    assert body["version"] == 2
    assert body["base_salary"] == "200000.00"
    assert body["results"]["income_before_tax"] == "230000.00"
    assert SalaryMonth.objects.count() == 1


def test_the_month_of_the_contract_with_its_items_has_the_balances_of_the_contract(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])
    add_item(
        client,
        account[1],
        body={"name": "Rent", "amount": "3000.00", "kind": "expense", "part": "first"},
    )
    add_item(
        client,
        account[1],
        body={
            "name": "Stipend",
            "amount": "1000.00",
            "kind": "income",
            "part": "first",
        },
    )
    add_item(
        client,
        account[1],
        body={
            "name": "Internet",
            "amount": "500.00",
            "kind": "expense",
            "part": "second",
        },
    )

    body = read_month(client, account[1]).json()

    assert body["fixed_totals"] == {
        "first": {"income": "1000.00", "expense": "3000.00"},
        "second": {"income": "0.00", "expense": "500.00"},
    }
    assert body["balances"] == {
        "first": "41500.00",
        "second": "60400.00",
        "month": "101900.00",
    }
    assert [i["name"] for i in body["fixed_items"]] == ["Rent", "Stipend", "Internet"]


def test_with_one_payment_every_item_counts_in_the_first_part_and_there_is_no_second(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(
        client,
        account[1],
        body=month_body(payment_mode="one_payment", working_days_first_half=7),
    )
    add_item(
        client,
        account[1],
        body={"name": "Rent", "amount": "3000.00", "kind": "expense", "part": "second"},
    )
    add_item(
        client,
        account[1],
        body={
            "name": "Stipend",
            "amount": "1000.00",
            "kind": "income",
            "part": "first",
        },
    )

    body = read_month(client, account[1]).json()

    assert body["working_days_first_half"] is None  # sent, ignored, stored as null
    assert body["payments"] == [{"part": "first", "amount": "104400.00"}]
    assert body["fixed_totals"] == {
        "first": {"income": "1000.00", "expense": "3000.00"},
        "second": None,
    }
    assert body["balances"] == {
        "first": "102400.00",
        "second": None,
        "month": "102400.00",
    }
    assert body["results"]["total_after_tax"] == "104400.00"


def test_a_one_payment_month_needs_no_first_half_at_all(
    client: APIClient, account: tuple[User, str]
) -> None:
    body = month_body(payment_mode="one_payment")
    del body["working_days_first_half"]

    assert put_month(client, account[1], body=body).status_code == 201


def test_the_balance_can_be_negative_and_is_written_with_a_minus(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1], body=month_body(payment_mode="one_payment"))
    add_item(
        client,
        account[1],
        body={"name": "Loan", "amount": "200000.00", "kind": "expense"},
    )

    balances = read_month(client, account[1]).json()["balances"]

    assert balances["first"] == "-95600.00"
    assert balances["month"] == "-95600.00"


@pytest.mark.parametrize(
    ("currency", "changes", "expected"),
    [
        ("JPY", {"base_salary": "250000", "food_per_day": "1000"}, "20000"),
        ("KWD", {"base_salary": "2500.000", "food_per_day": "1.500"}, "30.000"),
    ],
)
def test_the_amounts_have_the_digits_of_the_currency(
    client: APIClient,
    account: tuple[User, str],
    currency: str,
    changes: dict[str, str],
    expected: str,
) -> None:
    response = put_month(
        client, account[1], body=month_body(currency=currency, **changes)
    )

    assert response.status_code == 201
    assert response.json()["results"]["food_allowance"] == expected


@pytest.mark.parametrize(
    ("currency", "field", "value"),
    [
        ("RUB", "base_salary", "100000.0"),
        ("RUB", "base_salary", "100000.000"),
        ("RUB", "base_salary", "100000"),
        ("JPY", "base_salary", "250000.00"),
        ("KWD", "food_per_day", "1.50"),
    ],
)
def test_an_amount_with_the_wrong_digits_for_its_currency_is_invalid_amount(
    client: APIClient, account: tuple[User, str], currency: str, field: str, value: str
) -> None:
    right = {
        "RUB": {"base_salary": "100000.00", "food_per_day": "500.00"},
        "JPY": {"base_salary": "250000", "food_per_day": "1000"},
        "KWD": {"base_salary": "2500.000", "food_per_day": "1.500"},
    }[currency]
    body = month_body(currency=currency, **right)
    body[field] = value

    response = put_month(client, account[1], body=body)

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_amount"
    assert first_error(response)["attr"] == field


# --- validation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("currency", "XXX", "invalid_choice"),
        ("currency", "usd", "invalid_choice"),
        ("base_salary", "0.00", "invalid_amount"),
        ("base_salary", 100000, "invalid_amount"),
        ("base_salary", 100000.5, "invalid_amount"),
        ("base_salary", "-1.00", "invalid_amount"),
        ("base_salary", "1e5", "invalid_amount"),
        ("food_per_day", "-0.01", "invalid_amount"),
        ("working_days_in_month", 0, "min_value"),
        ("working_days_in_month", 32, "max_value"),
        ("working_days_in_month", "20", "invalid"),
        ("working_days_in_month", 20.0, "invalid"),
        ("working_days_in_month", True, "invalid"),
        ("working_hours_in_month", 0, "min_value"),
        ("working_hours_in_month", 745, "max_value"),
        ("extra_hours_hundredths", -1, "min_value"),
        ("extra_hours_hundredths", 74401, "max_value"),
        ("tax_rate_basis_points", -1, "min_value"),
        ("tax_rate_basis_points", 10001, "max_value"),
        ("payment_mode", "three_payments", "invalid_choice"),
        ("first_pay_day", 0, "min_value"),
        ("first_pay_day", 29, "max_value"),
        ("second_pay_day", 29, "max_value"),
        ("working_days_first_half", 21, "max_value"),
        ("working_days_first_half", -1, "min_value"),
    ],
)
def test_each_wrong_field_is_a_400_with_its_own_code(
    client: APIClient, account: tuple[User, str], field: str, value: Any, code: str
) -> None:
    response = put_month(client, account[1], body=month_body(**{field: value}))

    assert response.status_code == 400
    assert (first_error(response)["attr"], first_error(response)["code"]) == (
        field,
        code,
    )
    assert SalaryMonth.objects.count() == 0


def test_two_payments_need_the_first_half(
    client: APIClient, account: tuple[User, str]
) -> None:
    body = month_body()
    del body["working_days_first_half"]

    response = put_month(client, account[1], body=body)

    assert first_error(response)["code"] == "required"
    assert first_error(response)["attr"] == "working_days_first_half"
    assert (
        put_month(
            client, account[1], body=month_body(working_days_first_half=None)
        ).status_code
        == 400
    )


def test_the_first_half_may_be_every_working_day_or_none(
    client: APIClient, account: tuple[User, str]
) -> None:
    assert (
        put_month(
            client, account[1], "2026-09", month_body(working_days_first_half=20)
        ).status_code
        == 201
    )
    assert (
        put_month(
            client, account[1], "2026-08", month_body(working_days_first_half=0)
        ).status_code
        == 201
    )


def test_an_empty_body_reports_every_missing_field(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = put_month(client, account[1], body={})

    attrs = {e["attr"] for e in response.json()["errors"]}
    assert response.status_code == 400
    assert attrs >= {
        "currency",
        "base_salary",
        "food_per_day",
        "working_days_in_month",
        "working_hours_in_month",
        "extra_hours_hundredths",
        "tax_rate_basis_points",
        "payment_mode",
        "first_pay_day",
        "second_pay_day",
    }


def test_a_result_beyond_the_largest_amount_is_invalid_amount_and_stores_nothing(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = put_month(
        client, account[1], body=month_body(base_salary="9999999999999.99")
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_amount"
    assert first_error(response)["attr"] is None
    assert SalaryMonth.objects.count() == 0


def test_a_result_sent_by_the_client_is_ignored(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = put_month(
        client,
        account[1],
        body=month_body(tax="1.00", total_after_tax="1.00", version=99, id="x"),
    )

    assert response.status_code == 201
    assert response.json()["results"]["tax"] == "15600.00"
    assert response.json()["version"] == 1


# --- preconditions ----------------------------------------------------------------------------


def test_if_none_match_star_refuses_to_replace_a_month_that_exists(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])

    refused = put_month(
        client,
        account[1],
        body=month_body(base_salary="1.00"),
        headers={"If-None-Match": "*"},
    )

    assert refused.status_code == 412
    assert first_error(refused)["code"] == "precondition_failed"
    assert read_month(client, account[1]).json()["base_salary"] == "100000.00"


def test_if_none_match_star_creates_a_month_that_is_new(
    client: APIClient, account: tuple[User, str]
) -> None:
    assert (
        put_month(client, account[1], headers={"If-None-Match": "*"}).status_code == 201
    )


def test_if_match_compares_the_version_of_the_month(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])

    stale = put_month(
        client,
        account[1],
        body=month_body(base_salary="1.00"),
        headers={"If-Match": '"7"'},
    )
    fresh = put_month(
        client,
        account[1],
        body=month_body(base_salary="2.00"),
        headers={"If-Match": '"1"'},
    )

    assert (stale.status_code, fresh.status_code) == (412, 200)
    assert fresh.json()["base_salary"] == "2.00"


def test_if_match_on_a_month_that_does_not_exist_yet_is_a_412(
    client: APIClient, account: tuple[User, str]
) -> None:
    refused = put_month(client, account[1], headers={"If-Match": '"1"'})

    assert refused.status_code == 412
    assert SalaryMonth.objects.count() == 0


def test_the_currency_cannot_change_while_the_month_has_fixed_items(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])
    add_item(client, account[1])

    refused = put_month(client, account[1], body=month_body(currency="USD"))

    assert refused.status_code == 409
    assert first_error(refused)["code"] == "month_has_fixed_items"
    assert first_error(refused)["attr"] is None
    assert read_month(client, account[1]).json()["currency"] == "RUB"


def test_the_currency_can_change_while_there_are_no_items(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])

    changed = put_month(client, account[1], body=month_body(currency="USD"))

    assert changed.status_code == 200
    assert changed.json()["currency"] == "USD"


def test_saving_never_touches_the_items(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])
    item = add_item(client, account[1]).json()

    put_month(client, account[1], body=month_body(base_salary="150000.00"))

    items = read_month(client, account[1]).json()["fixed_items"]
    assert [(i["id"], i["version"]) for i in items] == [(item["id"], 1)]


# --- reading ----------------------------------------------------------------------------------


def test_a_saved_month_is_read_back_with_its_etag(
    client: APIClient, account: tuple[User, str]
) -> None:
    created = put_month(client, account[1]).json()

    response = read_month(client, account[1])

    assert response.status_code == 200
    assert response["ETag"] == '"1"'
    assert response.json() == created


def test_a_month_that_is_not_saved_is_a_404_with_the_json_body(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = read_month(client, account[1])

    assert response.status_code == 404
    assert response.json()["type"] == "client_error"


def test_a_stored_month_is_never_recalculated_when_it_is_read(
    client: APIClient, account: tuple[User, str]
) -> None:
    """History keeps the figures it was saved with (``calculation_version``), whatever the formulas become."""
    put_month(client, account[1])
    SalaryMonth.objects.update(tax=123456, total_after_tax=104400_00 - 123456 + 1560000)

    body = read_month(client, account[1]).json()

    assert body["results"]["tax"] == "1234.56"


@pytest.mark.parametrize(
    "key",
    [
        "2026-13",
        "2026-00",
        "1999-12",
        "2100-01",
        "2026-1",
        "26-10",
        "2026-10-01",
        "abc",
        "2026_10",
    ],
)
def test_a_month_key_that_is_not_in_range_is_not_a_route(
    client: APIClient, account: tuple[User, str], key: str
) -> None:
    for method in ("get", "put", "delete"):
        response = getattr(client, method)(
            f"{API}/months/{key}/", headers=bearer(account[1])
        )
        assert response.status_code == 404
        assert response["Content-Type"].startswith("text/html")


def test_the_edges_of_the_range_are_routes(
    client: APIClient, account: tuple[User, str]
) -> None:
    for key in ("2000-01", "2099-12"):
        assert put_month(client, account[1], key).status_code == 201


# --- deleting ---------------------------------------------------------------------------------


def test_deleting_a_month_deletes_its_items_too(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])
    add_item(client, account[1])
    add_item(
        client, account[1], body={"name": "Bonus", "amount": "10.00", "kind": "income"}
    )

    response = client.delete(f"{API}/months/2026-10/", headers=bearer(account[1]))

    assert response.status_code == 204
    assert SalaryMonth.objects.count() == 0
    assert FixedItem.objects.count() == 0


def test_deleting_a_month_that_is_not_saved_is_a_404(
    client: APIClient, account: tuple[User, str]
) -> None:
    assert (
        client.delete(f"{API}/months/2026-10/", headers=bearer(account[1])).status_code
        == 404
    )


def test_a_delete_from_a_stale_version_is_a_412_and_keeps_the_month(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])
    put_month(client, account[1], body=month_body(base_salary="2.00"))

    refused = client.delete(
        f"{API}/months/2026-10/", headers={**bearer(account[1]), "If-Match": '"1"'}
    )
    accepted = client.delete(
        f"{API}/months/2026-10/", headers={**bearer(account[1]), "If-Match": '"2"'}
    )

    assert (refused.status_code, accepted.status_code) == (412, 204)


# --- the history ------------------------------------------------------------------------------


def test_an_empty_history_is_200_with_an_empty_list(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = client.get(f"{API}/months/", headers=bearer(account[1]))

    assert response.status_code == 200
    assert response.json() == {"next": None, "previous": None, "results": []}


def test_the_history_is_newest_month_first_and_each_entry_has_everything(
    client: APIClient, account: tuple[User, str]
) -> None:
    for key in ("2026-08", "2026-10", "2026-09"):
        put_month(client, account[1], key)
    add_item(client, account[1], "2026-10")

    results = client.get(f"{API}/months/", headers=bearer(account[1])).json()["results"]

    assert [m["month"] for m in results] == ["2026-10", "2026-09", "2026-08"]
    assert results[0]["fixed_items"][0]["name"] == "Rent"
    assert results[0]["results"] == CASE_A["results"]
    assert results[0]["balances"]["month"] == "101400.00"
    assert results[1]["fixed_items"] == []


def test_the_history_is_paged_by_cursor(
    client: APIClient, account: tuple[User, str]
) -> None:
    for number in range(1, 6):
        put_month(client, account[1], f"2026-{number:02d}")

    first = client.get(
        f"{API}/months/", {"limit": 2}, headers=bearer(account[1])
    ).json()
    second = client.get(first["next"], headers=bearer(account[1])).json()
    third = client.get(second["next"], headers=bearer(account[1])).json()

    assert [m["month"] for m in first["results"]] == ["2026-05", "2026-04"]
    assert [m["month"] for m in second["results"]] == ["2026-03", "2026-02"]
    assert [m["month"] for m in third["results"]] == ["2026-01"]
    assert third["next"] is None


def test_the_history_costs_the_same_number_of_queries_whatever_it_holds(
    client: APIClient, account: tuple[User, str]
) -> None:
    """Each month's fixed items are fetched for the whole page at once, not one query per month."""

    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            response = client.get(f"{API}/months/", headers=bearer(account[1]))
        assert response.status_code == 200
        return len(queries)

    put_month(client, account[1], "2026-01")
    add_item(client, account[1], "2026-01")
    few = count()
    for number in range(2, 11):
        put_month(client, account[1], f"2026-{number:02d}")
        add_item(client, account[1], f"2026-{number:02d}")

    assert count() == few


def test_the_history_shows_only_the_own_months(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])
    stranger = APIClient()
    _, body = start_session(stranger)
    put_month(stranger, body["access_token"], "2026-03")

    mine = client.get(f"{API}/months/", headers=bearer(account[1])).json()["results"]
    theirs = stranger.get(
        f"{API}/months/", headers=bearer(body["access_token"])
    ).json()["results"]

    assert [m["month"] for m in mine] == ["2026-10"]
    assert [m["month"] for m in theirs] == ["2026-03"]


# --- other users and no token -----------------------------------------------------------------


def test_another_users_month_is_hidden_and_a_put_makes_the_callers_own(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1])
    stranger = APIClient()
    _, body = start_session(stranger)
    stranger.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")

    assert_hidden_from_other_users(
        stranger, f"{API}/months/2026-10/", methods=("get", "delete")
    )
    assert SalaryMonth.objects.count() == 1
    own = put_month(stranger, body["access_token"], body=month_body(base_salary="5.00"))

    assert own.status_code == 201
    assert SalaryMonth.objects.count() == 2
    assert read_month(client, account[1]).json()["base_salary"] == "100000.00"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/months/"),
        ("get", "/months/2026-10/"),
        ("put", "/months/2026-10/"),
        ("delete", "/months/2026-10/"),
        ("get", "/months/2026-10/defaults/"),
        ("post", "/months/2026-10/fixed-items/"),
        ("post", "/months/2026-10/fixed-items/copy/"),
        ("patch", "/fixed-items/0192a000-0000-7000-8000-000000000001/"),
        ("delete", "/fixed-items/0192a000-0000-7000-8000-000000000001/"),
    ],
)
def test_every_operation_needs_a_token(
    client: APIClient, method: str, path: str
) -> None:
    assert getattr(client, method)(f"{API}{path}", {}).status_code == 401


# --- the database says it too -----------------------------------------------------------------


def valid_row(user: User, **changes: Any) -> dict[str, Any]:
    import datetime

    row: dict[str, Any] = {
        "owner": user,
        "month": datetime.date(2026, 10, 1),
        "currency": "RUB",
        "base_salary": 100,
        "food_per_day": 0,
        "working_days_in_month": 20,
        "working_hours_in_month": 160,
        "extra_hours_hundredths": 0,
        "tax_rate_basis_points": 1300,
        "payment_mode": "one_payment",
        "working_days_first_half": None,
        "first_pay_day": 20,
        "second_pay_day": 5,
        "food_allowance": 0,
        "extra_hours_pay": 0,
        "income_before_tax": 100,
        "tax": 13,
        "total_after_tax": 87,
        "first_payment": 87,
        "second_payment": None,
    }
    return {**row, **changes}


def test_a_valid_row_is_accepted(account: tuple[User, str]) -> None:
    SalaryMonth.objects.create(**valid_row(account[0]))


@pytest.mark.parametrize(
    "changes",
    [
        {"base_salary": 0},
        {"base_salary": 10**15},
        {"food_per_day": -1},
        {"working_days_in_month": 0},
        {"working_days_in_month": 32},
        {"working_hours_in_month": 745},
        {"extra_hours_hundredths": 74401},
        {"tax_rate_basis_points": 10001},
        {"first_pay_day": 0},
        {"second_pay_day": 29},
        {"month": "2026-10-02"},
        {"month": "1999-12-01"},
        {"month": "2100-01-01"},
        {"payment_mode": "one_payment", "working_days_first_half": 5},
        {
            "payment_mode": "two_payments",
            "working_days_first_half": None,
            "second_payment": 1,
        },
        {
            "payment_mode": "two_payments",
            "working_days_first_half": 21,
            "second_payment": 0,
        },
        {"total_after_tax": -1},
        {"first_payment": 10**15},
    ],
    ids=str,
)
def test_the_database_refuses_a_row_the_rules_do_not_allow(
    account: tuple[User, str], changes: dict[str, Any]
) -> None:
    import datetime

    if isinstance(changes.get("month"), str):
        changes = {**changes, "month": datetime.date.fromisoformat(changes["month"])}

    with pytest.raises(IntegrityError):
        SalaryMonth.objects.create(**valid_row(account[0], **changes))


def test_a_month_is_unique_per_user(account: tuple[User, str]) -> None:
    SalaryMonth.objects.create(**valid_row(account[0]))

    with pytest.raises(IntegrityError):
        SalaryMonth.objects.create(**valid_row(account[0]))
