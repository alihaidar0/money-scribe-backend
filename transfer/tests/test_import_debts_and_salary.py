"""The CSV import of debts (with their repayments) and of salary months (with their fixed items)."""

import datetime
from typing import Any

import pytest
from rest_framework.response import Response
from rest_framework.test import APIClient

from debts.models import Debt, Repayment
from debts.tests.factories import DebtFactory, RepaymentFactory
from salary.models import FixedItem, SalaryMonth
from transfer.tests.helpers import import_rows
from users.models import SecurityEvent, User

pytestmark = pytest.mark.django_db


def debt_row(number: int, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "row": number,
        "direction": "lent",
        "person": "Alex",
        "amount": "200.00",
        "currency": "USD",
        "start_date": "2026-10-01",
        "due_date": "2026-11-01",
        "note": "Laptop repair",
        "repayments": [{"date": "2026-10-20", "amount": "50.00"}],
    }
    return {n: v for n, v in {**base, **fields}.items() if v is not None}


def month_row(number: int, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "row": number,
        "month": "2026-10",
        "currency": "RUB",
        "base_salary": "3000.00",
        "food_per_day": "5.00",
        "working_days_in_month": 22,
        "working_hours_in_month": 176,
        "extra_hours": "7.50",
        "tax_rate": "15.00",
        "payment_mode": "two_payments",
        "working_days_first_half": 11,
        "first_pay_day": 20,
        "second_pay_day": 5,
        "fixed_items": [
            {"name": "Rent", "amount": "300.00", "kind": "expense", "part": "first"}
        ],
    }
    return {n: v for n, v in {**base, **fields}.items() if v is not None}


def debts(
    client: APIClient, token: str, rows: list[dict[str, Any]], **kw: Any
) -> Response:
    return import_rows(client, token, "debts", rows, **kw)


def months(
    client: APIClient, token: str, rows: list[dict[str, Any]], **kw: Any
) -> Response:
    return import_rows(client, token, "salary_months", rows, **kw)


def problems(response: Response) -> list[tuple[int, str, str | None]]:
    return [(p["row"], p["code"], p["attr"]) for p in response.json()["problems"]]


# --- debts ------------------------------------------------------------------------------------


def test_a_debt_is_written_with_its_repayments(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = debts(
        client,
        me[1],
        [
            debt_row(
                2,
                repayments=[
                    {"date": "2026-10-20", "amount": "50.00"},
                    {"date": "2026-10-22", "amount": "25.00"},
                ],
            )
        ],
    )

    assert response.json() == {
        "dataset": "debts",
        "dry_run": False,
        "imported": 1,
        "skipped": 0,
        "problems": [],
    }
    debt = Debt.objects.get()
    assert (debt.owner, debt.direction, debt.person, debt.amount, debt.currency) == (
        me[0],
        "lent",
        "Alex",
        20000,
        "USD",
    )
    assert (debt.start_date, debt.due_date, debt.note) == (
        datetime.date(2026, 10, 1),
        datetime.date(2026, 11, 1),
        "Laptop repair",
    )
    assert sorted(r.amount for r in Repayment.objects.all()) == [2500, 5000]


def test_the_due_date_the_note_and_the_repayments_may_be_left_out(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = debts(
        client, me[1], [debt_row(2, due_date=None, note=None, repayments=None)]
    )

    assert response.json()["imported"] == 1
    debt = Debt.objects.get()
    assert (debt.due_date, debt.note, debt.repayments.count()) == (None, "", 0)


@pytest.mark.parametrize(
    ("changes", "code", "attr"),
    [
        ({"direction": "gave"}, "invalid_choice", "direction"),
        ({"person": None}, "required", "person"),
        ({"person": "x" * 51}, "max_length", "person"),
        ({"person": "bad\u0000"}, "invalid_characters", "person"),
        ({"amount": "0.00"}, "invalid_amount", "amount"),
        ({"amount": "200"}, "invalid_amount", "amount"),
        ({"currency": None}, "required", "currency"),
        ({"start_date": "2099-13-01"}, "invalid", "start_date"),
        ({"start_date": "2100-01-01"}, "date_out_of_range", "start_date"),
        ({"start_date": None}, "required", "start_date"),
        ({"due_date": "2026-09-30"}, "due_before_start", "due_date"),
        ({"note": "x" * 101}, "max_length", "note"),
        ({"id": "x"}, "read_only", "id"),
        ({"repaid": "10.00"}, "read_only", "repaid"),
        ({"status": "settled"}, "read_only", "status"),
        ({"repayments": "none"}, "not_a_list", "repayments"),
        ({"repayments": ["x"]}, "not_a_dict", "repayments.0"),
        (
            {
                "repayments": [
                    {"date": "2026-10-20", "amount": "50.00", "currency": "USD"}
                ]
            },
            "read_only",
            "repayments.0.currency",
        ),
        (
            {
                "repayments": [
                    {"date": "2026-10-20", "amount": "50.00"},
                    {"date": "2026-10-21", "amount": "150.01"},
                ]
            },
            "exceeds_remaining",
            "repayments.1.amount",
        ),
        (
            {"repayments": [{"date": "2026-09-30", "amount": "50.00"}]},
            "repayment_before_start",
            "repayments.0.date",
        ),
        (
            {"repayments": [{"date": "2026-10-20", "amount": "0.00"}]},
            "invalid_amount",
            "repayments.0.amount",
        ),
        ({"repayments": [{"date": "2026-10-20"}]}, "required", "repayments.0.amount"),
        ({"repayments": [{"amount": "5.00"}]}, "required", "repayments.0.date"),
        (
            {"repayments": [{"date": "2026-10-20", "amount": "5.000"}]},
            "invalid_amount",
            "repayments.0.amount",
        ),
    ],
)
def test_a_bad_debt_field_is_a_problem_on_that_field(
    client: APIClient,
    me: tuple[User, str],
    changes: dict[str, Any],
    code: str,
    attr: str,
) -> None:
    response = debts(client, me[1], [debt_row(2, **changes)])

    assert response.status_code == 200
    assert problems(response) == [(2, code, attr)]
    assert (
        Debt.objects.count() == 0
    )  # the debt of a failed row is rolled back with its repayments
    assert Repayment.objects.count() == 0


def test_a_failed_repayment_rolls_back_the_debt_but_not_the_other_rows(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = debts(
        client,
        me[1],
        [
            debt_row(2, person="First", repayments=[]),
            debt_row(
                3,
                person="Second",
                repayments=[{"date": "2026-10-02", "amount": "500.00"}],
            ),
            debt_row(
                4,
                person="Third",
                repayments=[{"date": "2026-10-02", "amount": "10.00"}],
            ),
        ],
    )

    assert problems(response) == [(3, "exceeds_remaining", "repayments.0.amount")]
    assert sorted(d.person for d in Debt.objects.all()) == ["First", "Third"]
    assert Repayment.objects.count() == 1


def test_a_debt_equal_to_an_existing_one_is_a_duplicate_whatever_its_repayments(
    client: APIClient, me: tuple[User, str]
) -> None:
    existing = DebtFactory.create(owner=me[0])
    RepaymentFactory.create(debt=existing, amount=100)

    response = debts(client, me[1], [debt_row(2, repayments=[])])

    assert problems(response) == [(2, "duplicate", None)]
    assert Debt.objects.count() == 1


def test_duplicates_of_debts_are_counted_by_multiplicity_and_a_missing_due_date_matches(
    client: APIClient, me: tuple[User, str]
) -> None:
    DebtFactory.create_batch(2, owner=me[0], due_date=None, note="")

    response = debts(
        client, me[1], [debt_row(n + 2, due_date=None, note=None) for n in range(3)]
    )

    assert response.json()["imported"] == 1
    assert Debt.objects.filter(owner=me[0]).count() == 3


def test_another_persons_debt_is_not_a_duplicate(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    DebtFactory.create(owner=other[0])

    assert debts(client, me[1], [debt_row(2)]).json()["imported"] == 1


def test_the_debt_cap_and_the_repayment_cap_skip_with_their_codes(
    client: APIClient, me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("debts.services.MAX_DEBTS", 2)
    monkeypatch.setattr("debts.services.MAX_REPAYMENTS", 1)
    DebtFactory.create(owner=me[0], person="Existing")

    response = debts(
        client,
        me[1],
        [
            debt_row(
                2,
                person="Two",
                repayments=[
                    {"date": "2026-10-02", "amount": "1.00"},
                    {"date": "2026-10-03", "amount": "1.00"},
                ],
            ),
            debt_row(3, person="Three", repayments=[]),
            debt_row(4, person="Four", repayments=[]),
        ],
    )

    assert problems(response) == [
        (2, "repayment_limit_reached", None),
        (4, "debt_limit_reached", None),
    ]
    assert sorted(d.person for d in Debt.objects.all()) == ["Existing", "Three"]


def test_a_debt_import_records_its_event_and_a_dry_run_writes_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    dry = debts(client, me[1], [debt_row(2)], dry_run=True)
    assert (dry.json()["imported"], Debt.objects.count()) == (1, 0)
    assert SecurityEvent.objects.filter(type="data_imported").count() == 0

    debts(client, me[1], [debt_row(2), debt_row(3, person="Sam")])

    event = SecurityEvent.objects.get(type="data_imported")
    assert (event.kinds, event.count) == (["debts"], 2)


# --- salary months ----------------------------------------------------------------------------


def test_a_salary_month_is_written_and_recomputed_with_its_fixed_items(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = months(client, me[1], [month_row(2)])

    assert response.json() == {
        "dataset": "salary_months",
        "dry_run": False,
        "imported": 1,
        "skipped": 0,
        "problems": [],
    }
    month = SalaryMonth.objects.get()
    assert (month.owner, month.month, month.currency) == (
        me[0],
        datetime.date(2026, 10, 1),
        "RUB",
    )
    # 7.50 hours and 15.00 percent are the stored hundredths and basis points, and the results are the ADR 0007 ones.
    assert (month.extra_hours_hundredths, month.tax_rate_basis_points) == (750, 1500)
    assert (month.total_after_tax, month.first_payment, month.second_payment) == (
        286083,
        127500,
        158583,
    )
    assert [(i.name, i.amount, i.kind, i.part) for i in FixedItem.objects.all()] == [
        ("Rent", 30000, "expense", "first")
    ]


def test_a_month_paid_once_needs_no_first_half_and_no_items(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = months(
        client,
        me[1],
        [
            month_row(
                2,
                payment_mode="one_payment",
                working_days_first_half=None,
                fixed_items=None,
            )
        ],
    )

    assert response.json()["imported"] == 1
    month = SalaryMonth.objects.get()
    assert (
        month.working_days_first_half,
        month.second_payment,
        month.fixed_items.count(),
    ) == (None, None, 0)


@pytest.mark.parametrize(
    ("changes", "code", "attr"),
    [
        ({"extra_hours": "7.5"}, "invalid", "extra_hours"),
        ({"extra_hours": "7.500"}, "invalid", "extra_hours"),
        ({"extra_hours": "7,50"}, "invalid", "extra_hours"),
        ({"extra_hours": 7.5}, "invalid", "extra_hours"),
        ({"extra_hours": "744.01"}, "max_value", "extra_hours"),
        ({"extra_hours": "-0.50"}, "min_value", "extra_hours"),
        ({"extra_hours": None}, "required", "extra_hours"),
        ({"tax_rate": "15"}, "invalid", "tax_rate"),
        ({"tax_rate": "100.01"}, "max_value", "tax_rate"),
        ({"tax_rate": "-1.00"}, "min_value", "tax_rate"),
        ({"tax_rate": None}, "required", "tax_rate"),
        ({"extra_hours_hundredths": 750}, "read_only", "extra_hours_hundredths"),
        ({"tax_rate_basis_points": 1500}, "read_only", "tax_rate_basis_points"),
        ({"id": "x"}, "read_only", "id"),
        ({"total_after_tax": "1.00"}, "read_only", "total_after_tax"),
        ({"month": None}, "required", "month"),
        ({"month": "2026-13"}, "invalid", "month"),
        ({"month": "1999-12"}, "invalid", "month"),
        ({"month": "2026-10-01"}, "invalid", "month"),
        ({"month": 202610}, "invalid", "month"),
        ({"currency": "XXX"}, "invalid_choice", "currency"),
        ({"base_salary": "0.00"}, "invalid_amount", "base_salary"),
        ({"base_salary": "3000"}, "invalid_amount", "base_salary"),
        ({"working_days_in_month": "22"}, "invalid", "working_days_in_month"),
        ({"working_days_in_month": 32}, "max_value", "working_days_in_month"),
        ({"working_hours_in_month": 0}, "min_value", "working_hours_in_month"),
        ({"payment_mode": "three"}, "invalid_choice", "payment_mode"),
        ({"working_days_first_half": None}, "required", "working_days_first_half"),
        ({"working_days_first_half": 23}, "max_value", "working_days_first_half"),
        ({"first_pay_day": 29}, "max_value", "first_pay_day"),
        ({"second_pay_day": None}, "required", "second_pay_day"),
        ({"fixed_items": "none"}, "invalid", "fixed_items"),
        ({"fixed_items": ["x"]}, "invalid", "fixed_items.0"),
        (
            {
                "fixed_items": [
                    {"name": "Rent", "amount": "300.00", "kind": "expense", "id": "x"}
                ]
            },
            "read_only",
            "fixed_items.0.id",
        ),
        (
            {"fixed_items": [{"name": "Rent", "amount": "300", "kind": "expense"}]},
            "invalid_amount",
            "fixed_items.0.amount",
        ),
        (
            {"fixed_items": [{"name": "Rent", "amount": "300.00", "kind": "gift"}]},
            "invalid_choice",
            "fixed_items.0.kind",
        ),
        (
            {"fixed_items": [{"amount": "300.00", "kind": "expense"}]},
            "required",
            "fixed_items.0.name",
        ),
        (
            {
                "fixed_items": [
                    {
                        "name": "Rent",
                        "amount": "300.00",
                        "kind": "expense",
                        "part": "third",
                    }
                ]
            },
            "invalid_choice",
            "fixed_items.0.part",
        ),
    ],
)
def test_a_bad_salary_field_is_a_problem_on_that_field(
    client: APIClient,
    me: tuple[User, str],
    changes: dict[str, Any],
    code: str,
    attr: str,
) -> None:
    response = months(client, me[1], [month_row(2, **changes)])

    assert response.status_code == 200
    assert problems(response) == [(2, code, attr)]
    assert SalaryMonth.objects.count() == 0
    assert FixedItem.objects.count() == 0


def test_the_largest_hours_and_percent_are_accepted(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = months(
        client, me[1], [month_row(2, extra_hours="744.00", tax_rate="100.00")]
    )

    assert response.json()["imported"] == 1
    month = SalaryMonth.objects.get()
    assert (month.extra_hours_hundredths, month.tax_rate_basis_points) == (74400, 10000)


def test_a_month_that_exists_is_a_duplicate_and_is_never_overwritten(
    client: APIClient, me: tuple[User, str]
) -> None:
    months(client, me[1], [month_row(2)])

    response = months(
        client,
        me[1],
        [
            month_row(2, base_salary="9999.00", fixed_items=[]),
            month_row(3, month="2026-11"),
        ],
    )

    assert problems(response) == [(2, "duplicate", "month")]
    assert response.json()["imported"] == 1
    saved = SalaryMonth.objects.get(month=datetime.date(2026, 10, 1))
    assert (saved.base_salary, saved.fixed_items.count()) == (300000, 1)


def test_the_same_month_twice_in_one_call_writes_the_first(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = months(client, me[1], [month_row(2), month_row(3, base_salary="1.00")])

    assert problems(response) == [(3, "duplicate", "month")]
    assert SalaryMonth.objects.get().base_salary == 300000


def test_another_persons_month_is_not_a_duplicate(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    months(client, other[1], [month_row(2)])

    assert months(client, me[1], [month_row(2)]).json()["imported"] == 1


def test_a_failed_item_rolls_back_the_month_of_that_row_only(
    client: APIClient, me: tuple[User, str]
) -> None:
    bad = [
        {"name": "Rent", "amount": "300.00", "kind": "expense"},
        {"name": "Tax", "amount": "x", "kind": "expense"},
    ]

    response = months(
        client,
        me[1],
        [month_row(2, month="2026-09", fixed_items=bad), month_row(3, month="2026-10")],
    )

    assert problems(response) == [(2, "invalid_amount", "fixed_items.1.amount")]
    assert [m.month for m in SalaryMonth.objects.all()] == [datetime.date(2026, 10, 1)]
    assert FixedItem.objects.count() == 1


def test_more_than_100_fixed_items_is_the_limit_code(
    client: APIClient, me: tuple[User, str]
) -> None:
    items = [
        {"name": f"Item {n}", "amount": "1.00", "kind": "expense"} for n in range(101)
    ]

    response = months(client, me[1], [month_row(2, fixed_items=items)])

    assert problems(response) == [(2, "fixed_item_limit_reached", None)]
    assert SalaryMonth.objects.count() == 0


def test_a_salary_month_with_sums_past_the_largest_amount_is_a_problem_not_a_crash(
    client: APIClient, me: tuple[User, str]
) -> None:
    items = [
        {"name": f"Item {n}", "amount": "9999999999999.99", "kind": "income"}
        for n in range(2)
    ]

    response = months(client, me[1], [month_row(2, fixed_items=items)])

    assert response.status_code == 200
    assert response.json()["skipped"] == 1
    assert SalaryMonth.objects.count() == 0


def test_a_salary_import_records_its_event(
    client: APIClient, me: tuple[User, str]
) -> None:
    months(client, me[1], [month_row(2), month_row(3, month="2026-11")])

    event = SecurityEvent.objects.get(type="data_imported")
    assert (event.kinds, event.count) == (["salary_months"], 2)
