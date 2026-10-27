"""The CSV export and import of accounts and custom categories: what makes a file restorable into an empty account."""

from typing import Any

import pytest
from django.db.models import Q
from rest_framework.response import Response
from rest_framework.test import APIClient

from ledger.models import (
    MAX_ACCOUNTS,
    MAX_CUSTOM_CATEGORIES,
    Account,
    Category,
    Transaction,
)
from transfer.tests.helpers import (
    export,
    import_rows,
    parse_csv,
    rows_of_csv,
    text_of,
    zip_members,
)
from users.models import SecurityEvent, User

pytestmark = pytest.mark.django_db


def problems(response: Response) -> list[tuple[int, str, str | None]]:
    return [(p["row"], p["code"], p["attr"]) for p in response.json()["problems"]]


def account_row(number: int, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"row": number, "name": "Savings", "kind": "other"}
    merged = {**base, **fields}
    return {name: value for name, value in merged.items() if value is not None}


def category_row(number: int, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"row": number, "name": "Pets", "icon": "gifts"}
    merged = {**base, **fields}
    return {name: value for name, value in merged.items() if value is not None}


def own_accounts(person: User) -> list[tuple[str, str]]:
    rows = Account.objects.filter(owner=person).order_by("created_at", "id")
    return [(a.name, a.kind) for a in rows]


def own_categories(person: User) -> list[tuple[str, str]]:
    rows = Category.objects.filter(owner=person).order_by("created_at", "id")
    return [(c.name, c.icon) for c in rows]


# --- importing accounts -----------------------------------------------------------------------


def test_accounts_are_created_and_the_others_are_reported(
    client: APIClient, me: tuple[User, str]
) -> None:
    before = own_accounts(me[0])
    rows = [
        account_row(2, name="Savings", kind="other"),
        account_row(3, name="Wallet", kind="cash"),
        account_row(4, name="Broken", kind="gold"),
        account_row(5, name=None),
        account_row(6, kind=None),
        account_row(7, name="Extra", id="x"),
    ]

    response = import_rows(client, me[1], "accounts", rows)

    assert response.status_code == 200
    assert (response.json()["imported"], response.json()["skipped"]) == (2, 4)
    assert problems(response) == [
        (4, "invalid_choice", "kind"),
        (5, "required", "name"),
        (6, "required", "kind"),
        (7, "read_only", "id"),
    ]
    assert own_accounts(me[0]) == [*before, ("Savings", "other"), ("Wallet", "cash")]


def test_an_account_name_is_trimmed_and_normalized_like_a_single_create(
    client: APIClient, me: tuple[User, str]
) -> None:
    decomposed = "Café"  # e + combining acute accent

    import_rows(
        client,
        me[1],
        "accounts",
        [account_row(2, name=f"  {decomposed}  ")],
    )

    assert ("Café", "other") in own_accounts(me[0])


def test_an_account_that_exists_in_any_case_is_a_duplicate_and_keeps_its_kind(
    client: APIClient, me: tuple[User, str]
) -> None:
    Account.objects.create(owner=me[0], name="Wallet", kind="cash")

    response = import_rows(
        client, me[1], "accounts", [account_row(2, name="WALLET", kind="card")]
    )

    assert problems(response) == [(2, "duplicate", "name")]
    assert Account.objects.get(owner=me[0], name="Wallet").kind == "cash"
    assert not Account.objects.filter(owner=me[0], name="WALLET").exists()


def test_a_repeat_inside_the_file_is_a_duplicate_of_the_first(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = import_rows(
        client,
        me[1],
        "accounts",
        [account_row(2, name="Vault"), account_row(3, name="vault")],
    )

    assert (response.json()["imported"], problems(response)) == (
        1,
        [(3, "duplicate", "name")],
    )


def test_importing_the_same_accounts_again_is_all_duplicates(
    client: APIClient, me: tuple[User, str]
) -> None:
    rows = [account_row(2, name="One"), account_row(3, name="Two")]
    import_rows(client, me[1], "accounts", rows)

    again = import_rows(client, me[1], "accounts", rows)

    assert again.json()["imported"] == 0
    assert {p["code"] for p in again.json()["problems"]} == {"duplicate"}


def test_accounts_beyond_the_cap_are_skipped_with_the_limit_code_and_a_duplicate_stays_a_duplicate(
    client: APIClient, me: tuple[User, str]
) -> None:
    room = MAX_ACCOUNTS - Account.objects.filter(owner=me[0]).count()
    Account.objects.create(owner=me[0], name="Existing", kind="card")
    room -= 1
    rows = [account_row(2 + i, name=f"A{i}") for i in range(room + 1)]
    rows.append(account_row(2 + room + 1, name="existing"))

    response = import_rows(client, me[1], "accounts", rows)

    assert response.json()["imported"] == room
    assert problems(response) == [
        (2 + room, "account_limit_reached", None),
        (2 + room + 1, "duplicate", "name"),
    ]
    assert Account.objects.filter(owner=me[0]).count() == MAX_ACCOUNTS


def test_a_dry_run_of_accounts_writes_nothing(
    client: APIClient, me: tuple[User, str]
) -> None:
    before = own_accounts(me[0])

    response = import_rows(client, me[1], "accounts", [account_row(2)], dry_run=True)

    assert (response.json()["dry_run"], response.json()["imported"]) == (True, 1)
    assert own_accounts(me[0]) == before


def test_another_persons_account_of_the_same_name_is_not_a_duplicate(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    Account.objects.create(owner=other[0], name="Savings", kind="card")

    response = import_rows(client, me[1], "accounts", [account_row(2)])

    assert response.json()["imported"] == 1
    assert Account.objects.get(owner=other[0], name="Savings").kind == "card"


# --- importing custom categories --------------------------------------------------------------


def test_categories_are_created_with_their_icon_and_the_kind_both(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = import_rows(
        client,
        me[1],
        "categories",
        [
            category_row(2, name="Pets", icon="gifts"),
            category_row(3, name="حيوانات", icon="travel"),
        ],
    )

    assert response.json()["imported"] == 2
    assert own_categories(me[0]) == [("Pets", "gifts"), ("حيوانات", "travel")]
    assert set(Category.objects.filter(owner=me[0]).values_list("kind", flat=True)) == {
        "both"
    }
    assert not Category.objects.filter(owner=me[0], key__isnull=False).exists()


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"icon": "rocket"}, ("invalid_choice", "icon")),
        ({"icon": None}, ("required", "icon")),  # there is no default icon
        ({"name": None}, ("required", "name")),
        (
            {"kind": "expense"},
            ("read_only", "kind"),
        ),  # a custom category is always both
        ({"name": "Groceries"}, ("name_taken", "name")),  # a built-in name
        ({"name": "groceries"}, ("name_taken", "name")),  # a built-in key
        ({"name": "x" * 31}, ("max_length", "name")),
    ],
)
def test_a_bad_category_is_a_problem_on_its_field_with_the_code_of_a_create(
    client: APIClient,
    me: tuple[User, str],
    fields: dict[str, Any],
    expected: tuple[str, str],
) -> None:
    response = import_rows(client, me[1], "categories", [category_row(2, **fields)])

    assert problems(response) == [(2, *expected)]
    assert own_categories(me[0]) == []


def test_a_category_that_exists_in_any_case_is_a_duplicate_and_keeps_its_icon(
    client: APIClient, me: tuple[User, str]
) -> None:
    Category.objects.create(owner=me[0], name="Pets", icon="health", kind="both")

    response = import_rows(
        client, me[1], "categories", [category_row(2, name="PETS", icon="travel")]
    )

    assert problems(response) == [(2, "duplicate", "name")]
    assert own_categories(me[0]) == [("Pets", "health")]


def test_categories_beyond_the_cap_are_skipped_with_the_limit_code(
    client: APIClient, me: tuple[User, str]
) -> None:
    rows = [category_row(2 + i, name=f"C{i}") for i in range(MAX_CUSTOM_CATEGORIES + 1)]

    response = import_rows(client, me[1], "categories", rows)

    assert response.json()["imported"] == MAX_CUSTOM_CATEGORIES
    assert problems(response) == [
        (2 + MAX_CUSTOM_CATEGORIES, "category_limit_reached", None)
    ]


def test_another_persons_category_of_the_same_name_is_not_a_duplicate(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    Category.objects.create(owner=other[0], name="Pets", icon="health", kind="both")

    response = import_rows(client, me[1], "categories", [category_row(2)])

    assert response.json()["imported"] == 1
    assert own_categories(other[0]) == [("Pets", "health")]


# --- the event --------------------------------------------------------------------------------


def test_a_call_that_creates_accounts_records_one_event_with_the_dataset_and_the_count(
    client: APIClient, me: tuple[User, str]
) -> None:
    import_rows(
        client,
        me[1],
        "accounts",
        [account_row(2, name="One"), account_row(3, name="Two")],
    )

    event = SecurityEvent.objects.get(user=me[0], type="data_imported")
    assert (event.kinds, event.count) == (["accounts"], 2)


# --- exporting --------------------------------------------------------------------------------


def test_accounts_are_exported_oldest_first_with_their_kind_and_no_period(
    client: APIClient, me: tuple[User, str]
) -> None:
    before = own_accounts(me[0])
    for name, kind in (("Zeta", "other"), ("=Alpha", "cash"), ('Mid, "x"', "card")):
        Account.objects.create(owner=me[0], name=name, kind=kind)

    response = export(
        client, me[1], ["accounts"], **{"from": "2090-01-01", "to": "2090-12-31"}
    )

    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert (
        response["Content-Disposition"]
        == 'attachment; filename="money-scribe-accounts.csv"'
    )
    assert parse_csv(text_of(response)) == [
        ["name", "kind"],
        *[[name, kind] for name, kind in before],
        ["Zeta", "other"],
        ["'=Alpha", "cash"],
        ['Mid, "x"', "card"],
    ]


def test_only_the_custom_categories_of_the_person_are_exported(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")
    Category.objects.create(owner=me[0], name="+Odd", icon="other", kind="both")
    Category.objects.create(owner=other[0], name="Theirs", icon="travel", kind="both")

    response = export(client, me[1], ["categories"])

    assert (
        response["Content-Disposition"]
        == 'attachment; filename="money-scribe-categories.csv"'
    )
    assert parse_csv(text_of(response)) == [
        ["name", "icon"],
        ["Pets", "gifts"],
        ["'+Odd", "other"],
    ]


def test_another_persons_accounts_are_not_in_the_file(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    Account.objects.create(owner=other[0], name="Secret vault", kind="other")

    assert "Secret vault" not in text_of(export(client, me[1], ["accounts"]))


def test_nothing_to_export_is_the_header_row_only_for_categories(
    client: APIClient, me: tuple[User, str]
) -> None:
    assert parse_csv(text_of(export(client, me[1], ["categories"]))) == [
        ["name", "icon"]
    ]


def test_a_zip_has_five_members_in_the_fixed_order_whatever_the_request_order(
    client: APIClient, me: tuple[User, str]
) -> None:
    asked = ["salary_months", "debts", "transactions", "categories", "accounts"]

    archive = zip_members(export(client, me[1], asked))

    assert list(archive) == [
        "accounts.csv",
        "categories.csv",
        "transactions.csv",
        "debts.csv",
        "salary_months.csv",
    ]
    # The export is limited to 5 an hour, so two of the five are compared with their single files.
    for kind in ("accounts", "categories"):
        assert archive[f"{kind}.csv"] == bytes(export(client, me[1], [kind]).content)


def test_the_export_event_names_the_kinds_as_asked(
    client: APIClient, me: tuple[User, str]
) -> None:
    export(client, me[1], ["categories", "accounts"])

    event = SecurityEvent.objects.get(user=me[0], type="data_exported")
    assert event.kinds == ["categories", "accounts"]


# --- the point of it all: a restore into an empty account -------------------------------------


def test_a_zip_restores_into_an_empty_account_in_the_documented_order(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    Account.objects.filter(owner=other[0]).delete()
    wallet = Account.objects.create(owner=me[0], name="Wallet", kind="cash")
    Account.objects.create(owner=me[0], name="=Savings", kind="other")
    pets = Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")
    for category, note in ((pets, "Food"), (Category.objects.get(key="groceries"), "")):
        Transaction.objects.create(
            owner=me[0],
            kind="expense",
            amount=1250,
            currency="USD",
            date="2026-10-24",
            category=category,
            account=wallet,
            note=note,
        )
    archive = zip_members(
        export(client, me[1], ["transactions", "accounts", "categories"])
    )

    # Into an empty account, a transaction row alone is refused; the datasets in order restore everything.
    refused = import_rows(
        client,
        other[1],
        "transactions",
        rows_of_csv("transactions", archive["transactions.csv"].decode("utf-8")[1:]),
    )
    assert {p["code"] for p in refused.json()["problems"]} == {
        "unknown_account",
        "unknown_category",
    }

    for dataset in ("accounts", "categories", "transactions"):
        text = archive[f"{dataset}.csv"].decode("utf-8")[1:]
        result = import_rows(client, other[1], dataset, rows_of_csv(dataset, text))
        assert result.json()["problems"] == [], dataset

    assert own_accounts(other[0]) == own_accounts(me[0])
    assert own_categories(other[0]) == own_categories(me[0])
    assert bytes(export(client, other[1], ["transactions"]).content) == bytes(
        export(client, me[1], ["transactions"]).content
    )
    assert (
        Transaction.objects.filter(Q(owner=other[0]), account__name="Wallet").count()
        == 2
    )


def test_the_export_the_import_and_the_document_know_the_same_datasets_in_one_order() -> (
    None
):
    from transfer import export as exporting
    from transfer import importing, schema_shapes

    order = ("accounts", "categories", "transactions", "debts", "salary_months")

    assert order == exporting.KINDS
    assert order == importing.DATASETS
    assert order == tuple(schema_shapes.BY_DATASET)
    assert order == tuple(exporting.HEADERS)
    assert set(exporting.CSV_NAMES) == set(exporting.ZIP_MEMBERS) == set(order)
