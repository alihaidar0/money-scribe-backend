"""The copy of fixed items and the defaults of a month (docs/api-fields.md)."""

import datetime
from typing import Any

import pytest
from rest_framework.test import APIClient

from idempotency.models import StoredResponse
from salary.models import MAX_FIXED_ITEMS, FixedItem, SalaryMonth
from salary.tests.helpers import (
    API,
    add_item,
    fresh_key,
    month_body,
    put_month,
    read_month,
)
from users.models import User
from users.tests.helpers import bearer, first_error, start_session

pytestmark = pytest.mark.django_db


def copy(
    client: APIClient,
    token: str,
    month: str = "2026-10",
    body: dict[str, Any] | None = None,
    key: str | None = None,
) -> Any:
    return client.post(
        f"{API}/months/{month}/fixed-items/copy/",
        {} if body is None else body,
        headers={**bearer(token), "Idempotency-Key": key or fresh_key()},
    )


def seed(client: APIClient, token: str, month: str, *names: str) -> None:
    put_month(client, token, month)
    for name in names:
        part = "second" if name.endswith("2") else "first"
        add_item(
            client,
            token,
            month,
            {
                "name": name,
                "amount": "100.00",
                "kind": "income" if name.startswith("pay") else "expense",
                "part": part,
            },
        )


# --- the copy ---------------------------------------------------------------------------------


def test_the_default_source_is_the_most_recent_saved_month_before_this_one(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    seed(client, token, "2026-08", "old rent")
    seed(client, token, "2026-09", "rent", "pay 2")
    seed(client, token, "2026-11", "later")
    put_month(client, token, "2026-10")

    response = copy(client, token)

    body = response.json()
    assert response.status_code == 200
    assert response["ETag"] == '"1"'
    assert body["month"] == "2026-10"
    assert [
        (i["name"], i["amount"], i["kind"], i["part"]) for i in body["fixed_items"]
    ] == [
        ("rent", "100.00", "expense", "first"),
        ("pay 2", "100.00", "income", "second"),
    ]


def test_the_items_are_new_rows_with_their_own_ids_and_version_one(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    seed(client, token, "2026-09", "rent")
    put_month(client, token, "2026-10")
    source_id = FixedItem.objects.get().id

    copy(client, token)

    ids = list(FixedItem.objects.order_by("created_at").values_list("id", flat=True))
    assert len(ids) == 2
    assert ids[1] != source_id
    assert set(FixedItem.objects.values_list("version", flat=True)) == {1}
    assert read_month(client, token, "2026-09").json()["fixed_items"][0]["id"] == str(
        source_id
    )


def test_an_explicit_source_is_used(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    seed(client, token, "2026-07", "from july")
    seed(client, token, "2026-09", "from september")
    put_month(client, token, "2026-10")

    body = copy(client, token, body={"from": "2026-07"}).json()

    assert [i["name"] for i in body["fixed_items"]] == ["from july"]


def test_the_copy_is_added_to_the_items_already_there(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    seed(client, token, "2026-09", "rent")
    seed(client, token, "2026-10", "already")

    body = copy(client, token).json()

    assert [i["name"] for i in body["fixed_items"]] == ["already", "rent"]


def test_a_source_with_no_items_copies_nothing_and_is_not_an_error(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    put_month(client, token, "2026-09")
    put_month(client, token, "2026-10")

    response = copy(client, token)

    assert response.status_code == 200
    assert response.json()["fixed_items"] == []


def test_a_repeat_with_the_same_key_copies_once(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    seed(client, token, "2026-09", "rent")
    put_month(client, token, "2026-10")
    key = fresh_key()

    first = copy(client, token, key=key)
    again = copy(client, token, key=key)

    assert again.json() == first.json()
    assert again["Idempotent-Replayed"] == "true"
    assert FixedItem.objects.count() == 2


def test_the_copy_needs_the_key(client: APIClient, account: tuple[User, str]) -> None:
    put_month(client, account[1], "2026-10")

    response = client.post(
        f"{API}/months/2026-10/fixed-items/copy/", {}, headers=bearer(account[1])
    )

    assert first_error(response)["code"] == "idempotency_key_required"


@pytest.mark.parametrize(
    ("month", "body"),
    [
        ("2026-10", None),  # no earlier month at all
        ("2026-10", {"from": "2026-05"}),  # the source is not saved
        ("2026-12", {}),  # the target is not saved
    ],
)
def test_a_month_or_a_source_that_is_not_saved_is_a_404(
    client: APIClient,
    account: tuple[User, str],
    month: str,
    body: dict[str, Any] | None,
) -> None:
    put_month(client, account[1], "2026-10")

    response = copy(client, account[1], month, body)

    assert response.status_code == 404
    assert FixedItem.objects.count() == 0


def test_a_source_of_another_user_is_not_there(
    client: APIClient, account: tuple[User, str]
) -> None:
    stranger = APIClient()
    _, other = start_session(stranger)
    seed(stranger, other["access_token"], "2026-09", "secret")
    put_month(client, account[1], "2026-10")

    response = copy(client, account[1], body={"from": "2026-09"})

    assert response.status_code == 404
    assert FixedItem.objects.count() == 1


def test_the_month_itself_is_not_a_source(
    client: APIClient, account: tuple[User, str]
) -> None:
    seed(client, account[1], "2026-10", "rent")

    response = copy(client, account[1], body={"from": "2026-10"})

    assert response.status_code == 400
    assert first_error(response)["attr"] == "from"
    assert FixedItem.objects.count() == 1


@pytest.mark.parametrize(
    "value", ["2026-13", "2026-9", "x", "1999-01", "2026-10-01", 202610, ""]
)
def test_a_source_that_is_not_a_month_key_is_a_400_on_from(
    client: APIClient, account: tuple[User, str], value: Any
) -> None:
    put_month(client, account[1], "2026-10")

    response = copy(client, account[1], body={"from": value})

    assert response.status_code == 400
    assert first_error(response)["attr"] == "from"


def test_a_source_of_another_currency_is_a_currency_mismatch_on_from(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    seed(client, token, "2026-09", "rent")
    put_month(client, token, "2026-10", month_body(currency="USD"))

    response = copy(client, token)

    assert response.status_code == 400
    assert (first_error(response)["code"], first_error(response)["attr"]) == (
        "currency_mismatch",
        "from",
    )
    assert FixedItem.objects.count() == 1


def test_a_copy_that_would_pass_a_hundred_items_copies_nothing(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    seed(client, token, "2026-09", "rent")
    put_month(client, token, "2026-10")
    target = SalaryMonth.objects.get(month="2026-10-01")
    FixedItem.objects.bulk_create(
        FixedItem(salary_month=target, name=f"n{n}", amount=1, kind="income")
        for n in range(MAX_FIXED_ITEMS)
    )

    key = fresh_key()
    response = copy(client, token, key=key)

    assert response.status_code == 409
    assert first_error(response)["code"] == "fixed_item_limit_reached"
    assert FixedItem.objects.filter(salary_month=target).count() == MAX_FIXED_ITEMS
    assert not StoredResponse.objects.filter(
        key=key.strip('"')
    ).exists()  # a refusal is not stored


def test_a_copy_that_fills_the_month_exactly_is_allowed(
    client: APIClient, account: tuple[User, str]
) -> None:
    token = account[1]
    seed(client, token, "2026-09", "rent")
    put_month(client, token, "2026-10")
    target = SalaryMonth.objects.get(month="2026-10-01")
    FixedItem.objects.bulk_create(
        FixedItem(salary_month=target, name=f"n{n}", amount=1, kind="income")
        for n in range(MAX_FIXED_ITEMS - 1)
    )

    assert copy(client, token).status_code == 200
    assert FixedItem.objects.filter(salary_month=target).count() == MAX_FIXED_ITEMS


# --- the defaults -----------------------------------------------------------------------------


def defaults(client: APIClient, token: str, month: str = "2026-10") -> Any:
    return client.get(f"{API}/months/{month}/defaults/", headers=bearer(token))


def test_with_no_month_saved_the_defaults_are_the_apps_own(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = defaults(client, account[1])

    assert response.status_code == 200
    assert response.json() == {
        "currency": "RUB",
        "base_salary": None,
        "food_per_day": None,
        "working_days_in_month": None,
        "working_hours_in_month": None,
        "extra_hours_hundredths": 0,
        "tax_rate_basis_points": 1300,
        "payment_mode": "one_payment",
        "working_days_first_half": None,
        "first_pay_day": 20,
        "second_pay_day": 5,
    }


def test_the_most_recent_month_carries_over_what_the_contract_says(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(
        client,
        account[1],
        "2026-09",
        month_body(
            currency="USD",
            base_salary="5000.00",
            food_per_day="10.00",
            working_hours_in_month=168,
            extra_hours_hundredths=300,
            tax_rate_basis_points=900,
            payment_mode="two_payments",
            working_days_first_half=9,
            first_pay_day=15,
            second_pay_day=3,
        ),
    )

    body = defaults(client, account[1]).json()

    assert body == {
        "currency": "USD",
        "base_salary": "5000.00",
        "food_per_day": "10.00",
        "working_days_in_month": None,  # never carried
        "working_hours_in_month": 168,
        "extra_hours_hundredths": 0,  # never carried
        "tax_rate_basis_points": 900,
        "payment_mode": "two_payments",
        "working_days_first_half": None,  # never carried
        "first_pay_day": 15,
        "second_pay_day": 3,
    }


def test_the_month_asked_for_is_never_its_own_source(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(
        client,
        account[1],
        "2026-10",
        month_body(base_salary="777.00", tax_rate_basis_points=500),
    )

    body = defaults(client, account[1], "2026-10").json()

    assert body["base_salary"] is None
    assert body["tax_rate_basis_points"] == 1300


def test_the_source_is_the_most_recent_other_month_even_a_later_one(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_month(client, account[1], "2026-08", month_body(base_salary="100.00"))
    put_month(client, account[1], "2026-09", month_body(base_salary="200.00"))

    assert defaults(client, account[1], "2026-10").json()["base_salary"] == "200.00"
    assert defaults(client, account[1], "2026-07").json()["base_salary"] == "200.00"
    assert defaults(client, account[1], "2026-09").json()["base_salary"] == "100.00"


def test_another_users_months_are_never_a_source(
    client: APIClient, account: tuple[User, str]
) -> None:
    stranger = APIClient()
    _, other = start_session(stranger)
    put_month(
        stranger, other["access_token"], "2026-09", month_body(base_salary="999.00")
    )

    assert defaults(client, account[1]).json()["base_salary"] is None


def test_the_amounts_do_not_carry_to_another_currency(
    client: APIClient, account: tuple[User, str]
) -> None:
    """When the Settings name another currency (a later step), a rouble amount is not offered as dollars."""
    from common.money import Currency
    from salary.services import defaults_for

    put_month(
        client,
        account[1],
        "2026-09",
        month_body(working_hours_in_month=168, tax_rate_basis_points=900),
    )

    other = defaults_for(
        account[0], datetime.date(2026, 10, 1), settings_currency=Currency.USD
    )
    same = defaults_for(
        account[0], datetime.date(2026, 10, 1), settings_currency=Currency.RUB
    )

    assert other["currency"] == "USD"
    assert other["base_salary"] is None and other["food_per_day"] is None
    assert (other["working_hours_in_month"], other["tax_rate_basis_points"]) == (
        168,
        900,
    )  # these still carry
    assert same["currency"] == "RUB"
    assert same["base_salary"] is not None and same["food_per_day"] is not None


def test_the_defaults_need_a_token_and_a_month_key(
    client: APIClient, account: tuple[User, str]
) -> None:
    assert client.get(f"{API}/months/2026-10/defaults/").status_code == 401
    assert defaults(client, account[1], "2026-13").status_code == 404
