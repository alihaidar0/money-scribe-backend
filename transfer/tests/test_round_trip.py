"""A file round-trips without loss: export, read the way the app does, import, export again."""

import json
import random
from pathlib import Path
from typing import Any

import pytest
from rest_framework.test import APIClient

from common.money import Currency, Money
from debts.models import Debt, Repayment
from ledger.models import Account, Category, Transaction
from salary.models import FixedItem, SalaryMonth
from salary.tests.helpers import add_item, month_body, put_month
from transfer.tests.helpers import BOM, export, import_rows, rows_of_csv, text_of
from users.models import User

pytestmark = pytest.mark.django_db

CASES = json.loads((Path(__file__).parent / "csv_vectors.json").read_text())[
    "round_trips"
]
RISKY = ["'", "=", "+", "-", "@", ",", '"', " ", "a", "b", "ب", ";", "x"]
CURRENCIES = [Currency.USD, Currency.JPY, Currency.KWD, Currency.RUB]
ACCOUNTS = ["Travel card", "Wallet", "=Cash"]


def prepare(
    person: User, accounts: list[str], categories: list[dict[str, str]]
) -> None:
    for name in accounts:
        Account.objects.create(owner=person, name=name, kind="cash")
    for spec in categories:
        Category.objects.create(
            owner=person, name=spec["name"], icon=spec["icon"], kind="both"
        )


def text(generator: random.Random, longest: int = 12) -> str:
    """A text a person could have saved: risky characters, trimmed (a saved text always is)."""
    return "".join(generator.choices(RISKY, k=generator.randint(0, longest))).strip()


def without_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in r.items() if k != "row"} for r in rows]


# --- the shared vectors -----------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_shared_round_trips(
    client: APIClient, me: tuple[User, str], case: dict[str, Any]
) -> None:
    prepare(me[0], case["accounts"], case["custom_categories"])

    imported = import_rows(client, me[1], case["dataset"], case["records"])
    assert imported.status_code == 200, imported.json()
    assert imported.json()["problems"] == []
    assert imported.json()["imported"] == len(case["records"])

    exported = export(client, me[1], [case["dataset"]])
    assert bytes(exported.content).decode("utf-8") == BOM + case["csv"]
    assert rows_of_csv(case["dataset"], case["csv"]) == case["records"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_importing_the_exported_file_again_is_all_duplicates(
    client: APIClient, me: tuple[User, str], case: dict[str, Any]
) -> None:
    prepare(me[0], case["accounts"], case["custom_categories"])
    import_rows(client, me[1], case["dataset"], case["records"])

    again = import_rows(
        client,
        me[1],
        case["dataset"],
        rows_of_csv(case["dataset"], text_of(export(client, me[1], [case["dataset"]]))),
    )

    assert again.json()["imported"] == 0
    assert {p["code"] for p in again.json()["problems"]} == {"duplicate"}


# --- a property test: whatever is saved comes back --------------------------------------------


def test_transactions_survive_the_trip_whatever_their_text(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    generator = random.Random(1025)  # noqa: S311  # a seeded generator for test data
    pets = [{"name": "Pets", "icon": "gifts"}, {"name": "=Odd", "icon": "gifts"}]
    for person in (me[0], other[0]):
        prepare(person, ACCOUNTS, pets)
    owned = {
        p.pk: {a.name: a for a in Account.objects.filter(owner=p)} for p in (me[0],)
    }
    categories = list(Category.objects.filter(owner__isnull=True)) + list(
        Category.objects.filter(owner=me[0])
    )
    for _ in range(80):
        category = generator.choice(categories)
        kind = generator.choice(["expense", "income"])
        if category.kind not in ("both", kind):
            kind = category.kind
        currency = generator.choice(CURRENCIES)
        Transaction.objects.create(
            owner=me[0],
            kind=kind,
            amount=generator.randint(1, 10**12),
            currency=currency.value,
            date=f"2026-{generator.randint(1, 12):02d}-{generator.randint(1, 28):02d}",
            category=category,
            account=owned[me[0].pk][generator.choice(ACCOUNTS)],
            note=text(generator, 30)[:100],
        )

    first = export(client, me[1], ["transactions"])
    rows = rows_of_csv("transactions", text_of(first))
    result = import_rows(client, other[1], "transactions", rows)

    assert result.json()["problems"] == []
    assert result.json()["imported"] == 80
    assert bytes(export(client, other[1], ["transactions"]).content) == bytes(
        first.content
    )
    assert sorted(t.note for t in Transaction.objects.filter(owner=other[0])) == sorted(
        t.note for t in Transaction.objects.filter(owner=me[0])
    )


def test_debts_survive_the_trip_with_their_repayments(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    generator = random.Random(1026)  # noqa: S311  # a seeded generator for test data
    for _ in range(40):
        currency = generator.choice(CURRENCIES)
        amount = generator.randint(100, 10**12)
        debt = Debt.objects.create(
            owner=me[0],
            direction=generator.choice(["lent", "borrowed"]),
            person=text(generator, 20)[:50] or "Someone",
            amount=amount,
            currency=currency.value,
            start_date=f"2026-{generator.randint(1, 6):02d}-{generator.randint(1, 28):02d}",
            due_date=generator.choice([None, "2027-01-15"]),
            note=text(generator, 30),
        )
        left = amount
        for _ in range(generator.randint(0, 5)):
            part = generator.randint(1, max(1, left // 3))
            if part > left:
                break
            left -= part
            Repayment.objects.create(
                debt=debt,
                amount=part,
                date=f"2026-{generator.randint(7, 12):02d}-{generator.randint(1, 28):02d}",
            )

    first = export(client, me[1], ["debts"])
    result = import_rows(
        client, other[1], "debts", rows_of_csv("debts", text_of(first))
    )

    assert result.json()["problems"] == []
    assert result.json()["imported"] == 40
    assert bytes(export(client, other[1], ["debts"]).content) == bytes(first.content)
    assert (
        Repayment.objects.filter(debt__owner=other[0]).count()
        == Repayment.objects.filter(debt__owner=me[0]).count()
    )


def test_salary_months_survive_the_trip_with_their_fixed_items(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    generator = random.Random(1027)  # noqa: S311  # a seeded generator for test data
    for number in range(1, 13):
        month = f"2026-{number:02d}"
        currency = generator.choice(CURRENCIES)
        days = generator.randint(1, 31)
        mode = generator.choice(["one_payment", "two_payments"])
        body = month_body(
            currency=currency.value,
            base_salary=Money(generator.randint(1, 10**9), currency).to_string(),
            food_per_day=Money(generator.randint(0, 10**5), currency).to_string(),
            working_days_in_month=days,
            working_hours_in_month=generator.randint(1, 744),
            extra_hours_hundredths=generator.randint(0, 74400),
            tax_rate_basis_points=generator.randint(0, 10000),
            payment_mode=mode,
            working_days_first_half=generator.randint(0, days)
            if mode == "two_payments"
            else None,
            first_pay_day=generator.randint(1, 28),
            second_pay_day=generator.randint(1, 28),
        )
        assert put_month(client, me[1], month, body).status_code == 201
        for _ in range(generator.randint(0, 4)):
            added = add_item(
                client,
                me[1],
                month,
                {
                    "name": text(generator, 20)[:50] or "Item",
                    "amount": Money(generator.randint(1, 10**8), currency).to_string(),
                    "kind": generator.choice(["income", "expense"]),
                    "part": generator.choice(["first", "second"]),
                },
            )
            assert added.status_code == 201, added.json()

    first = export(client, me[1], ["salary_months"])
    result = import_rows(
        client, other[1], "salary_months", rows_of_csv("salary_months", text_of(first))
    )

    assert result.json()["problems"] == []
    assert result.json()["imported"] == 12
    assert bytes(export(client, other[1], ["salary_months"]).content) == bytes(
        first.content
    )
    mine = {
        m.month: (
            m.total_after_tax,
            m.first_payment,
            m.second_payment,
            m.fixed_items.count(),
        )
        for m in SalaryMonth.objects.filter(owner=me[0])
    }
    theirs = {
        m.month: (
            m.total_after_tax,
            m.first_payment,
            m.second_payment,
            m.fixed_items.count(),
        )
        for m in SalaryMonth.objects.filter(owner=other[0])
    }
    assert mine == theirs
    assert (
        FixedItem.objects.filter(salary_month__owner=other[0]).count()
        == FixedItem.objects.filter(salary_month__owner=me[0]).count()
    )


def test_a_zip_export_holds_the_same_files_as_the_single_exports(
    client: APIClient, me: tuple[User, str]
) -> None:
    from transfer.tests.helpers import zip_members

    put_month(client, me[1], "2026-10", month_body())
    kinds = ["transactions", "debts", "salary_months"]

    archive = zip_members(export(client, me[1], kinds))

    for kind in kinds:
        assert archive[f"{kind}.csv"] == bytes(export(client, me[1], [kind]).content)
