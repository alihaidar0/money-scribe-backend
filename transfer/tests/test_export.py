"""The CSV export: the files, their columns and order, the password, the limits and the traces it leaves."""

import datetime
from typing import Any

import pytest
from django.core import mail
from django.test import Client
from rest_framework.test import APIClient

from debts.tests.factories import DebtFactory, RepaymentFactory
from ledger.models import Account, Category, Transaction
from salary.tests.helpers import add_item, month_body, put_month
from transfer.tests.helpers import (
    BOM,
    EXPORTS,
    export,
    parse_csv,
    text_of,
    zip_members,
)
from users import limits
from users.models import SecurityEvent, Session, User
from users.tests.helpers import PASSWORD, bearer, first_error
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db

TX_HEADER = [
    "date",
    "kind",
    "amount",
    "currency",
    "category_key",
    "category_name",
    "account_name",
    "note",
]


@pytest.fixture
def wallet(me: tuple[User, str]) -> Account:
    return Account.objects.create(owner=me[0], name="Wallet", kind="cash")


def spend(
    wallet: Account,
    date: str,
    amount: int = 1250,
    *,
    kind: str = "expense",
    currency: str = "USD",
    category: Category | None = None,
    note: str = "",
) -> Transaction:
    return Transaction.objects.create(
        owner=wallet.owner,
        kind=kind,
        amount=amount,
        currency=currency,
        date=date,
        category=category or Category.objects.get(key="groceries"),
        account=wallet,
        note=note,
    )


# --- one file ---------------------------------------------------------------------------------


def test_one_kind_is_a_csv_file_with_a_byte_order_mark_and_fixed_headers(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-24", note="Weekly shop")

    response = export(client, me[1], ["transactions"])

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert (
        response["Content-Disposition"]
        == 'attachment; filename="money-scribe-transactions.csv"'
    )
    assert response["Cache-Control"] == "no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    raw = bytes(response.content)
    assert raw.startswith(BOM.encode("utf-8"))
    text = raw.decode("utf-8")
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert text[len(BOM) :] == (
        "date,kind,amount,currency,category_key,category_name,account_name,note\r\n"
        "2026-10-24,expense,12.50,USD,groceries,,Wallet,Weekly shop\r\n"
    )


@pytest.mark.parametrize("accept", ["text/csv", "application/json", "*/*", "image/png"])
def test_the_file_is_answered_whatever_the_client_accepts(
    client: APIClient, me: tuple[User, str], accept: str
) -> None:
    response = client.post(
        EXPORTS,
        {"kinds": ["debts"], "current_password": PASSWORD},
        headers={**bearer(me[1]), "Accept": accept},
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv; charset=utf-8"


def test_nothing_in_the_period_is_the_header_row_only(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-24")

    response = export(
        client, me[1], ["transactions"], **{"from": "2027-01-01", "to": "2027-12-31"}
    )

    assert response.status_code == 200
    assert parse_csv(text_of(response)) == [TX_HEADER]


@pytest.mark.parametrize("kind", ["transactions", "debts", "salary_months"])
def test_a_person_with_no_data_gets_the_header_row(
    client: APIClient, me: tuple[User, str], kind: str
) -> None:
    lines = parse_csv(text_of(export(client, me[1], [kind])))

    assert len(lines) == 1


def test_the_rows_are_oldest_first_and_the_entry_added_first_comes_first_within_a_day(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-26", 300)
    first = spend(wallet, "2026-10-24", 100)
    second = spend(wallet, "2026-10-24", 200)

    rows = parse_csv(text_of(export(client, me[1], ["transactions"])))[1:]

    assert [r[2] for r in rows] == ["1.00", "2.00", "3.00"]
    assert first.created_at <= second.created_at


def test_a_built_in_category_has_its_key_and_a_custom_one_its_name(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    pets = Category.objects.create(owner=me[0], name="Pets", icon="gifts", kind="both")
    spend(wallet, "2026-10-24", category=Category.objects.get(key="dining"))
    spend(wallet, "2026-10-25", category=pets)

    rows = parse_csv(text_of(export(client, me[1], ["transactions"])))

    assert rows[1][4:6] == ["dining", ""]
    assert rows[2][4:6] == ["", "Pets"]


def test_amounts_have_the_digits_of_their_currency(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-01", 1200, currency="JPY")
    spend(wallet, "2026-10-02", 3250, currency="KWD")
    spend(wallet, "2026-10-03", 5, currency="USD")

    rows = parse_csv(text_of(export(client, me[1], ["transactions"])))[1:]

    assert [(r[2], r[3]) for r in rows] == [
        ("1200", "JPY"),
        ("3.250", "KWD"),
        ("0.05", "USD"),
    ]


def test_a_text_cell_that_could_be_a_formula_is_written_with_a_quote(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    other = Account.objects.create(owner=me[0], name="+Card", kind="card")
    spend(wallet, "2026-10-01", note="=1+1")
    spend(other, "2026-10-02", note="@home")
    spend(wallet, "2026-10-03", note="'=x")
    spend(wallet, "2026-10-04", note="a=b")

    rows = parse_csv(text_of(export(client, me[1], ["transactions"])))[1:]

    assert [r[7] for r in rows] == ["'=1+1", "'@home", "''=x", "a=b"]
    assert rows[1][6] == "'+Card"


def test_quotes_commas_and_arabic_are_quoted_the_csv_way(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-01", note='He said "hi", and left')
    spend(wallet, "2026-10-02", note="كتاب")

    text = text_of(export(client, me[1], ["transactions"]))

    assert '"He said ""hi"", and left"' in text
    assert "كتاب" in text
    assert [r[7] for r in parse_csv(text)[1:]] == ['He said "hi", and left', "كتاب"]


def test_the_period_applies_to_the_date_inclusive(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    for day in ("2026-09-30", "2026-10-01", "2026-10-31", "2026-11-01"):
        spend(wallet, day)

    both = parse_csv(
        text_of(
            export(
                client,
                me[1],
                ["transactions"],
                **{"from": "2026-10-01", "to": "2026-10-31"},
            )
        )
    )
    only_from = parse_csv(
        text_of(export(client, me[1], ["transactions"], **{"from": "2026-10-31"}))
    )
    only_to = parse_csv(
        text_of(export(client, me[1], ["transactions"], to="2026-10-01"))
    )

    assert [r[0] for r in both[1:]] == ["2026-10-01", "2026-10-31"]
    assert [r[0] for r in only_from[1:]] == ["2026-10-31", "2026-11-01"]
    assert [r[0] for r in only_to[1:]] == ["2026-09-30", "2026-10-01"]


def test_another_persons_records_are_not_in_the_file(
    client: APIClient, me: tuple[User, str], other: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-01")
    theirs = Account.objects.create(owner=other[0], name="Theirs", kind="cash")
    spend(theirs, "2026-10-01", 999, note="secret")
    DebtFactory.create(owner=other[0], person="Secret")
    put_month(client, other[1])

    for kind, expected_rows in (
        ("transactions", 1),
        ("debts", 0),
        ("salary_months", 0),
    ):
        text = text_of(export(client, me[1], [kind]))
        assert "secret" not in text.lower()
        assert len(parse_csv(text)) - 1 == expected_rows


# --- debts and salary months ------------------------------------------------------------------


def test_a_debt_has_its_repayments_as_a_json_cell(
    client: APIClient, me: tuple[User, str]
) -> None:
    debt = DebtFactory.create(owner=me[0])
    RepaymentFactory.create(debt=debt, amount=5000, date=datetime.date(2026, 10, 22))
    RepaymentFactory.create(debt=debt, amount=2500, date=datetime.date(2026, 10, 20))
    DebtFactory.create(
        owner=me[0], due_date=None, note="", start_date=datetime.date(2026, 10, 5)
    )

    text = text_of(export(client, me[1], ["debts"]))
    rows = parse_csv(text)

    assert rows[0] == [
        "direction",
        "person",
        "amount",
        "currency",
        "start_date",
        "due_date",
        "note",
        "repayments",
    ]
    assert rows[1] == [
        "lent",
        "Alex",
        "200.00",
        "USD",
        "2026-10-01",
        "2026-11-01",
        "Laptop repair",
        '[{"date":"2026-10-20","amount":"25.00"},{"date":"2026-10-22","amount":"50.00"}]',
    ]
    assert rows[2][5:] == ["", "", "[]"]


def test_the_period_of_debts_is_the_start_date(
    client: APIClient, me: tuple[User, str]
) -> None:
    DebtFactory.create(
        owner=me[0], start_date=datetime.date(2026, 10, 1), due_date=None
    )
    DebtFactory.create(
        owner=me[0], start_date=datetime.date(2026, 12, 1), due_date=None
    )

    rows = parse_csv(text_of(export(client, me[1], ["debts"], **{"to": "2026-11-30"})))

    assert [r[4] for r in rows[1:]] == ["2026-10-01"]


def test_a_salary_month_has_readable_hours_and_percent_and_no_results(
    client: APIClient, me: tuple[User, str]
) -> None:
    put_month(
        client,
        me[1],
        "2026-10",
        month_body(
            base_salary="3000.00",
            food_per_day="5.00",
            working_days_in_month=22,
            working_hours_in_month=176,
            extra_hours_hundredths=750,
            tax_rate_basis_points=1500,
            working_days_first_half=11,
        ),
    )
    add_item(
        client,
        me[1],
        "2026-10",
        {"name": "Rent", "amount": "300.00", "kind": "expense", "part": "first"},
    )

    rows = parse_csv(text_of(export(client, me[1], ["salary_months"])))

    assert rows[0] == [
        "month",
        "currency",
        "base_salary",
        "food_per_day",
        "working_days_in_month",
        "working_hours_in_month",
        "extra_hours",
        "tax_rate",
        "payment_mode",
        "working_days_first_half",
        "first_pay_day",
        "second_pay_day",
        "fixed_items",
    ]
    assert rows[1] == [
        "2026-10",
        "RUB",
        "3000.00",
        "5.00",
        "22",
        "176",
        "7.50",
        "15.00",
        "two_payments",
        "11",
        "20",
        "5",
        '[{"name":"Rent","amount":"300.00","kind":"expense","part":"first"}]',
    ]


def test_a_fixed_item_with_a_day_exports_it_and_one_without_has_no_day_key(
    client: APIClient, me: tuple[User, str]
) -> None:
    put_month(client, me[1], "2026-10", month_body())
    add_item(
        client,
        me[1],
        "2026-10",
        {"name": "Rent", "amount": "300.00", "kind": "expense", "day": 31},
    )
    add_item(
        client,
        me[1],
        "2026-10",
        {"name": "Gym", "amount": "20.00", "kind": "expense", "part": "second"},
    )

    rows = parse_csv(text_of(export(client, me[1], ["salary_months"])))

    assert rows[1][-1] == (
        '[{"name":"Rent","amount":"300.00","kind":"expense","part":"first","day":31},'
        '{"name":"Gym","amount":"20.00","kind":"expense","part":"second"}]'
    )


def test_a_salary_month_paid_once_has_an_empty_first_half(
    client: APIClient, me: tuple[User, str]
) -> None:
    put_month(
        client,
        me[1],
        "2026-10",
        month_body(payment_mode="one_payment", working_days_first_half=None),
    )

    row = parse_csv(text_of(export(client, me[1], ["salary_months"])))[1]

    assert (row[8], row[9], row[12]) == ("one_payment", "", "[]")


def test_the_period_of_salary_months_is_counted_in_months(
    client: APIClient, me: tuple[User, str]
) -> None:
    for month in ("2026-09", "2026-10", "2026-11"):
        put_month(client, me[1], month, month_body())

    rows = parse_csv(
        text_of(
            export(
                client,
                me[1],
                ["salary_months"],
                **{"from": "2026-10-20", "to": "2026-10-21"},
            )
        )
    )

    assert [r[0] for r in rows[1:]] == ["2026-10"]


# --- several kinds ----------------------------------------------------------------------------


def test_several_kinds_are_a_zip_with_one_file_per_kind_in_a_fixed_order(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-24")

    response = export(client, me[1], ["salary_months", "transactions", "debts"])

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert (
        response["Content-Disposition"]
        == 'attachment; filename="money-scribe-export.zip"'
    )
    assert response["Cache-Control"] == "no-store"
    members = zip_members(response)
    # Whatever the order asked, the members are in the order of a restore.
    assert list(members) == ["transactions.csv", "debts.csv", "salary_months.csv"]
    assert all(body.startswith(BOM.encode("utf-8")) for body in members.values())
    assert len(parse_csv(members["transactions.csv"].decode("utf-8")[1:])) == 2


def test_the_archive_says_nothing_about_when_it_was_made(
    client: APIClient, me: tuple[User, str]
) -> None:
    import io
    import zipfile

    response = export(client, me[1], ["transactions", "debts"])

    with zipfile.ZipFile(io.BytesIO(bytes(response.content))) as archive:
        assert {info.date_time for info in archive.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }


def test_the_same_data_gives_the_same_bytes(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-24")

    first = bytes(export(client, me[1], ["transactions", "debts"]).content)
    second = bytes(export(client, me[1], ["transactions", "debts"]).content)

    assert first == second


# --- the body ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kinds", "code"),
    [
        ([], "min_length"),
        (["transactions", "transactions"], "invalid_choice"),
        (["nope"], "invalid_choice"),
        (
            [
                "accounts",
                "categories",
                "transactions",
                "debts",
                "salary_months",
                "accounts",
            ],
            "max_length",
        ),
        ("transactions", "invalid"),
        (None, "null"),
    ],
)
def test_bad_kinds_are_a_400_on_kinds(
    client: APIClient, me: tuple[User, str], kinds: Any, code: str
) -> None:
    response = export(client, me[1], kinds)

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == (code, "kinds")
    assert len(mail.outbox) == 0


def test_a_missing_kinds_is_required(client: APIClient, me: tuple[User, str]) -> None:
    response = client.post(
        EXPORTS, {"current_password": PASSWORD}, headers=bearer(me[1])
    )

    assert (first_error(response)["code"], first_error(response)["attr"]) == (
        "required",
        "kinds",
    )


def test_from_after_to_is_invalid_date_range_on_to(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = export(
        client, me[1], ["debts"], **{"from": "2026-10-02", "to": "2026-10-01"}
    )

    error = first_error(response)
    assert (response.status_code, error["code"], error["attr"]) == (
        400,
        "invalid_date_range",
        "to",
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("from", "1999-12-31", "date_out_of_range"),
        ("to", "2100-01-01", "date_out_of_range"),
        ("from", "2026-02-30", "invalid"),
        ("to", "yesterday", "invalid"),
    ],
)
def test_a_bad_day_is_a_400_on_that_day(
    client: APIClient, me: tuple[User, str], field: str, value: str, code: str
) -> None:
    response = export(client, me[1], ["debts"], **{field: value})

    error = first_error(response)
    assert (response.status_code, error["code"], error["attr"]) == (400, code, field)


def test_a_missing_password_is_required(
    client: APIClient, me: tuple[User, str]
) -> None:
    response = client.post(EXPORTS, {"kinds": ["debts"]}, headers=bearer(me[1]))

    error = first_error(response)
    assert (response.status_code, error["code"], error["attr"]) == (
        400,
        "required",
        "current_password",
    )


# --- the password and the limits --------------------------------------------------------------


def test_a_wrong_password_is_invalid_password_and_exports_nothing(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-24")

    response = export(
        client, me[1], ["transactions"], current_password="not-my-password"
    )

    error = first_error(response)
    assert (response.status_code, error["code"], error["attr"]) == (
        400,
        "invalid_password",
        "current_password",
    )
    assert b"Wallet" not in response.content
    assert SecurityEvent.objects.filter(type="data_exported").count() == 0
    assert len(mail.outbox) == 0


def test_five_wrong_passwords_lock_the_export_and_the_password_change_together(
    client: APIClient, me: tuple[User, str]
) -> None:
    for _ in range(4):
        assert (
            export(client, me[1], ["debts"], current_password="nope").status_code == 400
        )

    fifth = export(client, me[1], ["debts"], current_password="nope")
    locked = export(client, me[1], ["debts"])

    assert fifth.status_code == 429
    assert first_error(fifth)["code"] == "password_attempts_exceeded"
    assert int(fifth["Retry-After"]) >= 1
    assert locked.status_code == 429  # the right password is refused while locked


def test_the_export_is_limited_to_five_an_hour(
    client: APIClient, me: tuple[User, str]
) -> None:
    codes = [export(client, me[1], ["debts"]).status_code for _ in range(6)]

    assert codes == [200, 200, 200, 200, 200, 429]
    refused = export(client, me[1], ["debts"])
    assert first_error(refused)["code"] == "throttled"
    assert int(refused["Retry-After"]) >= 1


def test_the_limit_is_per_person(
    client: APIClient, me: tuple[User, str], other: tuple[User, str]
) -> None:
    for _ in range(6):
        export(client, me[1], ["debts"])

    assert export(client, other[1], ["debts"]).status_code == 200


def test_while_the_limit_store_is_down_the_export_is_503(
    client: APIClient, me: tuple[User, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(limits, "cache", BrokenCache())

    response = export(client, me[1], ["debts"])

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"
    assert response["Retry-After"] == "30"
    assert SecurityEvent.objects.filter(type="data_exported").count() == 0


def test_an_export_needs_a_signed_in_person(client: APIClient) -> None:
    assert (
        client.post(
            EXPORTS, {"kinds": ["debts"], "current_password": PASSWORD}
        ).status_code
        == 401
    )


def test_only_post_is_offered(client: APIClient, me: tuple[User, str]) -> None:
    assert client.get(EXPORTS, headers=bearer(me[1])).status_code == 405


# --- what it leaves behind --------------------------------------------------------------------


def test_an_export_writes_a_security_event_with_the_kinds_and_the_row_count(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-24")
    spend(wallet, "2026-10-25")
    DebtFactory.create(owner=me[0])
    session = Session.objects.get(user=me[0])

    export(client, me[1], ["transactions", "debts", "salary_months"])

    event = SecurityEvent.objects.get(type="data_exported")
    assert event.user == me[0]
    assert event.kinds == ["transactions", "debts", "salary_months"]
    assert event.count == 3
    assert event.session_id == session.id
    assert event.device_name == session.device_name


def test_the_event_holds_no_value(
    client: APIClient, me: tuple[User, str], wallet: Account
) -> None:
    spend(wallet, "2026-10-24", 987654, note="private note")

    export(client, me[1], ["transactions"])

    event = SecurityEvent.objects.get(type="data_exported")
    text = " ".join(
        str(v) for v in (event.kinds, event.count, event.device_name, event.platform)
    )
    assert "private" not in text and "987654" not in text and "Wallet" not in text


def test_the_owner_gets_an_email_with_the_device_and_the_time_and_nothing_else(
    client: APIClient, me: tuple[User, str], wallet: Account, clock: Any
) -> None:
    spend(wallet, "2026-10-24", note="private note")
    User.objects.filter(pk=me[0].pk).update(time_zone="Europe/Moscow")
    session = Session.objects.get(user=me[0])

    export(client, me[1], ["transactions"])

    [message] = mail.outbox
    assert message.to == [me[0].email]
    assert session.device_name in message.body
    assert (
        "2026-10-15 15:00 Europe/Moscow" in message.body
    )  # the frozen 12:00 UTC, in the person's zone
    assert "private" not in message.body and "Wallet" not in message.body
    assert "http" not in message.body


def test_the_email_is_in_the_language_of_the_account(
    client: APIClient, me: tuple[User, str]
) -> None:
    User.objects.filter(pk=me[0].pk).update(language="ar")

    export(client, me[1], ["debts"])

    [message] = mail.outbox
    assert "تم تصدير" in message.subject


def test_nothing_of_the_content_is_logged(
    client: APIClient,
    me: tuple[User, str],
    wallet: Account,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spend(wallet, "2026-10-24", 987654, note="private note")
    DebtFactory.create(owner=me[0], person="Secret person")

    with caplog.at_level("DEBUG"):
        export(client, me[1], ["transactions", "debts"])

    assert "private note" not in caplog.text
    assert "Secret person" not in caplog.text
    assert "9876.54" not in caplog.text


def test_a_plain_django_client_gets_the_same_file(me: tuple[User, str]) -> None:
    response = Client().post(
        EXPORTS,
        {"kinds": ["debts"], "current_password": PASSWORD},
        content_type="application/json",
        headers=bearer(me[1]),
    )

    assert response.status_code == 200
