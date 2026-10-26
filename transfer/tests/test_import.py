"""The CSV import of transactions: rows, problems, duplicates, dry runs, idempotency and the limits."""

from typing import Any

import pytest
from rest_framework.response import Response
from rest_framework.test import APIClient

from idempotency.models import StoredResponse
from ledger.models import Account, Category, Transaction
from transfer.tests.helpers import IMPORTS, fresh_key, import_rows
from users import limits
from users.models import SecurityEvent, User
from users.tests.helpers import bearer, first_error
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db


@pytest.fixture
def card(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Travel card", kind="card")


def row(number: Any, **fields: Any) -> dict[str, Any]:
    """A correct transaction row; a test changes a field, or removes one with ``None``."""
    base: dict[str, Any] = {
        "row": number,
        "date": "2026-10-24",
        "kind": "expense",
        "amount": "12.50",
        "currency": "USD",
        "category_key": "groceries",
        "account_name": "Travel card",
        "note": "Weekly shop",
    }
    merged = {**base, **fields}
    return {name: value for name, value in merged.items() if value is not None}


def run(
    client: APIClient, token: str, rows: list[dict[str, Any]], **kwargs: Any
) -> Response:
    return import_rows(client, token, "transactions", rows, **kwargs)


def problems(response: Response) -> list[tuple[int, str, str | None]]:
    return [(p["row"], p["code"], p["attr"]) for p in response.json()["problems"]]


# --- the example of the contract --------------------------------------------------------------


def test_rows_are_written_and_the_others_are_reported(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")
    rows = [
        row(2),
        row(
            3,
            date="2026-10-25",
            kind="income",
            amount="50.00",
            category_key="other",
            note="=Refund",
        ),
        row(
            4,
            date=None,
            category_key=None,
            category_name="Pets",
            amount="8.00",
            note="Food",
        ),
        row(
            5,
            date="2026-10-26",
            category_key="dining",
            account_name="Old wallet",
            amount="3.00",
            note="",
        ),
    ]

    response = run(client, me[1], rows)

    assert response.status_code == 200
    assert response.json() == {
        "dataset": "transactions",
        "dry_run": False,
        "imported": 2,
        "skipped": 2,
        "problems": [
            {"row": 4, "code": "required", "attr": "date"},
            {"row": 5, "code": "unknown_account", "attr": "account_name"},
        ],
    }
    written = Transaction.objects.order_by("created_at")
    assert [(t.amount, t.note, t.account) for t in written] == [
        (1250, "Weekly shop", card),
        (5000, "=Refund", card),
    ]


def test_the_problems_are_ordered_by_row_whatever_the_order_sent(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    rows = [row(9, kind="x"), row(3, amount="0.00"), row(7, account_name="Nope")]

    response = run(client, me[1], rows)

    assert [p["row"] for p in response.json()["problems"]] == [3, 7, 9]


def test_every_row_skipped_is_200_with_nothing_imported(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = run(client, me[1], [row(2, amount="x"), row(3, kind="x")])

    assert response.status_code == 200
    assert (response.json()["imported"], response.json()["skipped"]) == (0, 2)
    assert Transaction.objects.count() == 0


def test_a_row_has_at_most_one_problem_the_first(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = run(client, me[1], [row(2, amount="x", kind="x", date="nope")])

    assert len(response.json()["problems"]) == 1


# --- the rules of a single create ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "code", "attr"),
    [
        ({"amount": "0.00"}, "invalid_amount", "amount"),
        ({"amount": "12.5"}, "invalid_amount", "amount"),
        ({"amount": 12.5}, "invalid_amount", "amount"),
        ({"amount": "-1.00"}, "invalid_amount", "amount"),
        ({"amount": None}, "required", "amount"),
        ({"currency": "XXX"}, "invalid_choice", "currency"),
        ({"currency": None}, "required", "currency"),
        ({"kind": "gift"}, "invalid_choice", "kind"),
        ({"kind": None}, "required", "kind"),
        ({"date": "2026-02-30"}, "invalid", "date"),
        ({"date": "1999-12-31"}, "date_out_of_range", "date"),
        ({"date": None}, "required", "date"),
        ({"note": "x" * 101}, "max_length", "note"),
        ({"note": "line\nbreak"}, "invalid_characters", "note"),
        ({"note": "‮evil"}, "invalid_characters", "note"),
        ({"account_name": None}, "required", "account_name"),
        ({"account_name": "x" * 31}, "max_length", "account_name"),
        ({"category_key": None}, "required", "category_key"),
        ({"category_key": "nonsense"}, "unknown_category", "category_key"),
        (
            {"category_key": None, "category_name": "Nope"},
            "unknown_category",
            "category_name",
        ),
        ({"category_name": "Pets"}, "invalid", "category_name"),
        ({"id": "0198f2b0-4e5a-7d12-a3c4-6b7e8f9a0c1d"}, "read_only", "id"),
        ({"version": 1}, "read_only", "version"),
        ({"owner": "x"}, "read_only", "owner"),
        (
            {"category_id": "0198f2a0-0000-7000-8000-000000000001"},
            "read_only",
            "category_id",
        ),
    ],
)
def test_a_bad_field_is_a_problem_on_that_field_with_the_code_of_a_create(
    client: APIClient,
    me: tuple[User, str],
    card: Account,
    changes: dict[str, Any],
    code: str,
    attr: str,
) -> None:
    Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")

    response = run(client, me[1], [row(2, **changes)])

    assert response.status_code == 200
    assert problems(response) == [(2, code, attr)]
    assert Transaction.objects.count() == 0


def test_a_category_that_does_not_allow_the_kind_is_a_problem_on_the_column_used(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = run(
        client,
        me[1],
        [
            row(2, kind="income", category_key="dining"),
            row(3, kind="expense", category_key="scholarship"),
        ],
    )

    assert problems(response) == [
        (2, "category_not_allowed", "category_key"),
        (3, "category_not_allowed", "category_key"),
    ]


def test_a_category_is_found_by_its_key_among_the_built_in_ones_and_by_its_name_among_the_persons_own(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], card: Account
) -> None:
    Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")
    Category.objects.create(owner=other[0], name="Secret", icon="gifts", kind="both")

    response = run(
        client,
        me[1],
        [
            row(2, category_key=None, category_name="pets"),  # any case
            row(
                3, category_key=None, category_name="Groceries"
            ),  # a built-in is by its key only
            row(4, category_key=None, category_name="Secret"),  # another person's
            row(5, category_key="Groceries"),  # the key is exact
        ],
    )

    assert problems(response) == [
        (3, "unknown_category", "category_name"),
        (4, "unknown_category", "category_name"),
        (5, "unknown_category", "category_key"),
    ]
    assert response.json()["imported"] == 1


def test_an_account_is_found_by_its_name_in_any_case_and_never_created(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = run(
        client, me[1], [row(2, account_name="TRAVEL CARD"), row(3, account_name="Cash")]
    )

    assert problems(response) == [(3, "unknown_account", "account_name")]
    assert Account.objects.filter(owner=me[0]).count() == 1
    assert Category.objects.filter(owner=me[0]).count() == 0


def test_another_persons_account_is_unknown(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    Account.objects.create(owner=other[0], name="Travel card", kind="card")

    response = run(client, me[1], [row(2)])

    assert problems(response) == [(2, "unknown_account", "account_name")]


def test_text_rules_apply_trimming_and_normalizing(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    run(client, me[1], [row(2, note="  Café  ", account_name="  Travel card ")])

    assert Transaction.objects.get().note == "Café"


def test_an_empty_cell_is_a_missing_field_and_the_note_defaults_to_empty(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = run(client, me[1], [row(2, note=None)])

    assert response.json()["imported"] == 1
    assert Transaction.objects.get().note == ""


# --- duplicates -------------------------------------------------------------------------------


def make(card: Account, **fields: Any) -> Transaction:
    values: dict[str, Any] = {
        "owner": card.owner,
        "kind": "expense",
        "amount": 1250,
        "currency": "USD",
        "date": "2026-10-24",
        "category": Category.objects.get(key="groceries"),
        "account": card,
        "note": "Weekly shop",
    }
    return Transaction.objects.create(**{**values, **fields})


def test_a_row_equal_to_an_existing_record_is_a_duplicate(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    make(card)

    response = run(client, me[1], [row(2)])

    assert problems(response) == [(2, "duplicate", None)]
    assert Transaction.objects.count() == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"date": "2026-10-25"},
        {"kind": "income", "category_key": "other"},
        {"amount": "12.51"},
        {"currency": "EUR"},
        {"category_key": "dining"},
        {"note": "Weekly shop!"},
    ],
)
def test_a_row_that_differs_in_any_field_is_not_a_duplicate(
    client: APIClient, me: tuple[User, str], card: Account, changes: dict[str, Any]
) -> None:
    make(card)

    response = run(client, me[1], [row(2, **changes)])

    assert response.json()["imported"] == 1


def test_a_row_on_another_account_is_not_a_duplicate(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    make(Account.objects.create(owner=me[0], name="Wallet", kind="cash"))

    assert run(client, me[1], [row(2)]).json()["imported"] == 1


@pytest.mark.parametrize(
    ("existing", "in_file", "imported"),
    [(0, 1, 1), (0, 2, 2), (2, 3, 1), (2, 2, 0), (3, 2, 0), (1, 3, 2)],
)
def test_duplicates_are_counted_by_multiplicity(
    client: APIClient,
    me: tuple[User, str],
    card: Account,
    existing: int,
    in_file: int,
    imported: int,
) -> None:
    for _ in range(existing):
        make(card)

    response = run(client, me[1], [row(n + 2) for n in range(in_file)])

    assert response.json()["imported"] == imported
    assert response.json()["skipped"] == in_file - imported
    assert all(code == "duplicate" for _, code, _ in problems(response))
    assert Transaction.objects.count() == existing + imported


def test_the_first_rows_of_a_kind_are_the_duplicates_and_the_later_ones_are_written(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    make(card)

    response = run(client, me[1], [row(2), row(3), row(4)])

    assert problems(response) == [(2, "duplicate", None)]


def test_a_repeat_in_a_later_call_is_a_duplicate_never_silently_lost(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    rows = [row(2), row(3, amount="1.00")]

    first = run(client, me[1], rows)
    second = run(client, me[1], rows)

    assert first.json()["imported"] == 2
    assert (second.json()["imported"], second.json()["skipped"]) == (0, 2)
    assert Transaction.objects.count() == 2


def test_another_persons_records_do_not_make_a_duplicate(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], card: Account
) -> None:
    theirs = Account.objects.create(owner=other[0], name="Travel card", kind="card")
    make(theirs)

    assert run(client, me[1], [row(2)]).json()["imported"] == 1


# --- dry run ----------------------------------------------------------------------------------


def test_a_dry_run_answers_what_a_real_call_would_and_writes_nothing(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    make(card)
    rows = [row(2), row(3, amount="1.00"), row(4, amount="2.00"), row(5, kind="x")]

    dry = run(client, me[1], rows, dry_run=True)
    assert Transaction.objects.count() == 1
    real = run(client, me[1], rows)

    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True and real.json()["dry_run"] is False
    assert {**dry.json(), "dry_run": False} == real.json()
    assert (dry.json()["imported"], dry.json()["skipped"]) == (2, 2)


def test_a_dry_run_needs_no_key_stores_nothing_and_records_no_event(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = client.post(
        IMPORTS,
        {"dataset": "transactions", "dry_run": True, "rows": [row(2)]},
        headers=bearer(me[1]),
    )

    assert response.status_code == 200
    assert StoredResponse.objects.count() == 0
    assert SecurityEvent.objects.filter(type="data_imported").count() == 0
    assert Transaction.objects.count() == 0


def test_a_dry_run_cannot_see_rows_of_earlier_calls_but_counts_the_database(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    make(card)

    dry = run(client, me[1], [row(2)], dry_run=True)

    assert problems(dry) == [(2, "duplicate", None)]


# --- idempotency ------------------------------------------------------------------------------


def test_a_real_import_needs_an_idempotency_key(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = client.post(
        IMPORTS,
        {"dataset": "transactions", "rows": [row(2)]},
        headers=bearer(me[1]),
    )

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (
        "idempotency_key_required",
        "idempotency_key",
    )
    assert Transaction.objects.count() == 0


def test_a_malformed_key_is_idempotency_key_invalid(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = client.post(
        IMPORTS,
        {"dataset": "transactions", "rows": [row(2)]},
        headers={**bearer(me[1]), "Idempotency-Key": "short"},
    )

    assert first_error(response)["code"] == "idempotency_key_invalid"


def test_the_same_key_and_body_replays_the_first_answer_and_writes_once(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    key = fresh_key()
    rows = [row(2), row(3, amount="1.00")]

    first = run(client, me[1], rows, key=key)
    again = run(client, me[1], rows, key=key)

    assert again["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert Transaction.objects.count() == 2
    assert SecurityEvent.objects.filter(type="data_imported").count() == 1


def test_the_same_key_with_another_body_is_422(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    key = fresh_key()
    run(client, me[1], [row(2)], key=key)

    response = run(client, me[1], [row(2, amount="9.99")], key=key)

    assert response.status_code == 422
    assert first_error(response)["code"] == "idempotency_key_reused"
    assert Transaction.objects.count() == 1


# --- the body ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        ([], "min_length"),
        ("rows", "invalid"),
        (None, "null"),
        ([{"date": "2026-10-24"}], "invalid"),
        ([row(0)], "invalid"),
        ([row(-1)], "invalid"),
        ([row("2")], "invalid"),
        ([row(True)], "invalid"),
        ([row(1.5)], "invalid"),
        ([row(2), row(2)], "invalid"),
        (["a row"], "invalid"),
        ([None], "invalid"),
    ],
)
def test_bad_rows_are_a_400_on_rows(
    client: APIClient, me: tuple[User, str], card: Account, rows: Any, code: str
) -> None:
    response = import_rows(client, me[1], "transactions", rows)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, "rows")
    assert Transaction.objects.count() == 0


def test_500_rows_are_accepted_and_501_is_max_length(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    ok = run(client, me[1], [row(n + 2, amount=f"{n + 1}.00") for n in range(500)])
    too_many = run(client, me[1], [row(n + 2) for n in range(501)])

    assert (ok.status_code, ok.json()["imported"]) == (200, 500)
    assert too_many.status_code == 400
    assert first_error(too_many)["code"] == "max_length"


def test_an_unknown_dataset_is_invalid_choice(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = import_rows(client, me[1], "accounts", [row(2)])

    error = first_error(response)
    assert (response.status_code, error["code"], error["attr"]) == (
        400,
        "invalid_choice",
        "dataset",
    )


def test_a_missing_dataset_or_rows_is_required(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = client.post(
        IMPORTS, {}, headers={**bearer(me[1]), "Idempotency-Key": fresh_key()}
    )

    assert {(e["code"], e["attr"]) for e in response.json()["errors"]} == {
        ("required", "dataset"),
        ("required", "rows"),
    }


def test_dry_run_must_be_a_boolean(client: APIClient, me: tuple[User, str]) -> None:
    response = client.post(
        IMPORTS,
        {"dataset": "transactions", "dry_run": "yes", "rows": [row(2)]},
        headers={**bearer(me[1]), "Idempotency-Key": fresh_key()},
    )

    assert response.status_code == 400
    assert first_error(response)["attr"] == "dry_run"


def test_a_body_over_1_mb_is_request_too_large(
    client: APIClient, me: tuple[User, str]
) -> None:
    big = row(2)
    big["note"] = "x" * (1024 * 1024 + 10)

    response = client.post(
        IMPORTS,
        {"dataset": "transactions", "rows": [big]},
        headers={**bearer(me[1]), "Idempotency-Key": fresh_key()},
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "request_too_large"


def test_an_import_needs_a_signed_in_person(client: APIClient) -> None:
    assert (
        client.post(IMPORTS, {"dataset": "transactions", "rows": [row(2)]}).status_code
        == 401
    )


# --- the limits -------------------------------------------------------------------------------


def test_rows_beyond_the_cap_are_skipped_with_the_limit_code_and_the_rest_are_written(
    client: APIClient,
    me: tuple[User, str],
    card: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ledger.services.MAX_TRANSACTIONS", 4)
    make(card, amount=1)
    make(card, amount=2)

    response = run(client, me[1], [row(n + 2, amount=f"{n + 10}.00") for n in range(4)])

    assert (response.json()["imported"], response.json()["skipped"]) == (2, 2)
    assert problems(response) == [
        (4, "transaction_limit_reached", None),
        (5, "transaction_limit_reached", None),
    ]
    assert Transaction.objects.filter(owner=me[0]).count() == 4


def test_a_duplicate_is_reported_as_a_duplicate_even_at_the_cap(
    client: APIClient,
    me: tuple[User, str],
    card: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ledger.services.MAX_TRANSACTIONS", 1)
    make(card)

    assert problems(run(client, me[1], [row(2)])) == [(2, "duplicate", None)]


def test_the_import_is_limited_to_120_calls_an_hour_dry_runs_included(
    client: APIClient, me: tuple[User, str], card: Account, settings: Any
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "import": "3/hour",
        },
    }

    codes = [
        run(client, me[1], [row(2)], dry_run=index % 2 == 0).status_code
        for index in range(4)
    ]

    assert codes == [200, 200, 200, 429]
    refused = run(client, me[1], [row(2)], dry_run=True)
    assert (
        first_error(refused)["code"] == "throttled" and int(refused["Retry-After"]) >= 1
    )


def test_while_the_limit_store_is_down_the_import_is_503(
    client: APIClient,
    me: tuple[User, str],
    card: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(limits, "cache", BrokenCache())

    response = run(client, me[1], [row(2)], dry_run=True)

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"
    assert Transaction.objects.count() == 0


# --- the events and the one transaction -----------------------------------------------------------


def test_a_call_that_writes_records_one_event_with_the_dataset_and_the_count(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    run(client, me[1], [row(2), row(3, amount="1.00"), row(4, kind="x")])

    event = SecurityEvent.objects.get(type="data_imported")
    assert (event.kinds, event.count, event.user) == (["transactions"], 2, me[0])
    assert event.device_name


def test_a_call_that_skips_every_row_records_no_event(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    run(client, me[1], [row(2, kind="x")])

    assert SecurityEvent.objects.filter(type="data_imported").count() == 0


def test_a_failed_call_records_no_event(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    import_rows(client, me[1], "transactions", [])

    assert SecurityEvent.objects.filter(type="data_imported").count() == 0


def test_a_crash_in_the_middle_leaves_nothing_half_written(
    client: APIClient,
    me: tuple[User, str],
    card: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ledger import services

    original = services.create_transaction
    calls = {"n": 0}

    def crash(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("the database went away")
        return original(*args, **kwargs)

    monkeypatch.setattr(services, "create_transaction", crash)

    response = run(client, me[1], [row(n + 2, amount=f"{n + 1}.00") for n in range(5)])

    assert response.status_code == 500
    assert Transaction.objects.count() == 0
    assert SecurityEvent.objects.filter(type="data_imported").count() == 0
    assert StoredResponse.objects.count() == 0


def test_a_failed_row_leaves_nothing_of_its_own_and_does_not_stop_the_others(
    client: APIClient, me: tuple[User, str], card: Account
) -> None:
    response = run(
        client,
        me[1],
        [
            row(2, amount="1.00"),
            row(3, kind="income", category_key="dining"),
            row(4, amount="3.00"),
        ],
    )

    assert response.json()["imported"] == 2
    assert sorted(t.amount for t in Transaction.objects.all()) == [100, 300]


def test_nothing_of_the_rows_is_logged(
    client: APIClient,
    me: tuple[User, str],
    card: Account,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("DEBUG"):
        run(
            client,
            me[1],
            [
                row(2, note="private note", amount="98765.43"),
                row(3, account_name="Secret place"),
            ],
        )

    assert "private note" not in caplog.text
    assert "98765" not in caplog.text
    assert "Secret place" not in caplog.text
