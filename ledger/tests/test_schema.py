"""What the OpenAPI document promises about accounts and categories (ADR 0004, 0016, 0017)."""

from typing import Any

import pytest
from drf_spectacular.generators import SchemaGenerator

from ledger.defaults import CUSTOM_ICONS

ACCOUNTS = "/api/v1/accounts/"
ACCOUNT = "/api/v1/accounts/{id}/"
CATEGORIES = "/api/v1/categories/"
CATEGORY = "/api/v1/categories/{id}/"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def op(schema: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["paths"][path][method]
    return found


def params(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in operation.get("parameters", [])}


def component(schema: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["components"]["schemas"][name]
    return found


def test_the_operations_have_the_ids_and_tags_the_client_calls_them_by(
    schema: dict[str, Any],
) -> None:
    found = {
        (path, method): (body["operationId"], body["tags"])
        for path, methods in schema["paths"].items()
        if path.startswith((ACCOUNTS, CATEGORIES))
        for method, body in methods.items()
    }

    assert found == {
        (ACCOUNTS, "get"): ("account_list", ["accounts"]),
        (ACCOUNTS, "post"): ("account_create", ["accounts"]),
        (ACCOUNT, "get"): ("account_read", ["accounts"]),
        (ACCOUNT, "patch"): ("account_update", ["accounts"]),
        (ACCOUNT, "delete"): ("account_delete", ["accounts"]),
        (CATEGORIES, "get"): ("category_list", ["categories"]),
        (CATEGORIES, "post"): ("category_create", ["categories"]),
        (CATEGORY, "get"): ("category_read", ["categories"]),
        (CATEGORY, "patch"): ("category_update", ["categories"]),
        (CATEGORY, "delete"): ("category_delete", ["categories"]),
    }


def test_every_operation_needs_the_bearer_token(schema: dict[str, Any]) -> None:
    for path in (ACCOUNTS, ACCOUNT, CATEGORIES, CATEGORY):
        for body in schema["paths"][path].values():
            assert body["security"] == [{"bearerAuth": []}], path


@pytest.mark.parametrize("path", [ACCOUNTS, CATEGORIES])
def test_the_creates_require_the_idempotency_key_and_declare_409_and_422(
    schema: dict[str, Any], path: str
) -> None:
    operation = op(schema, path, "post")

    assert params(operation)["Idempotency-Key"]["required"] is True
    assert {"201", "400", "401", "409", "422", "429"} <= operation["responses"].keys()
    assert "ETag" in operation["responses"]["201"]["headers"]
    assert "Idempotent-Replayed" in operation["responses"]["201"]["headers"]


@pytest.mark.parametrize("path", [ACCOUNT, CATEGORY])
def test_a_change_and_a_delete_take_if_match_and_declare_412(
    schema: dict[str, Any], path: str
) -> None:
    for method in ("patch", "delete"):
        operation = op(schema, path, method)

        assert params(operation)["If-Match"].get("required", False) is False
        assert "412" in operation["responses"]
    assert "ETag" in op(schema, path, "patch")["responses"]["200"]["headers"]


def test_deleting_an_account_declares_the_409_for_an_account_with_transactions(
    schema: dict[str, Any],
) -> None:
    responses = op(schema, ACCOUNT, "delete")["responses"]

    assert {"204", "409", "412", "404"} <= responses.keys()
    assert "content" not in responses["204"]
    assert "account_has_transactions" in op(schema, ACCOUNT, "delete")["description"]


def test_deleting_a_category_is_204_and_a_built_in_one_is_a_403(
    schema: dict[str, Any],
) -> None:
    operation = op(schema, CATEGORY, "delete")

    assert {"204", "403", "404", "412"} <= operation["responses"].keys()
    assert (
        "409" not in operation["responses"]
    )  # ADR 0017: the transactions move to other
    assert "category_is_builtin" in operation["description"]


def test_the_lists_are_the_paginated_envelope_with_limit_and_cursor(
    schema: dict[str, Any],
) -> None:
    for path, name in ((ACCOUNTS, "Account"), (CATEGORIES, "Category")):
        operation = op(schema, path, "get")
        envelope = component(schema, f"Paginated{name}List")

        assert {"limit", "cursor"} <= params(operation).keys()
        assert set(envelope["required"]) == {"next", "previous", "results"}
        assert envelope["properties"]["results"]["items"] == {
            "$ref": f"#/components/schemas/{name}"
        }


def test_an_account_has_a_kind_of_three_and_a_name_of_up_to_30(
    schema: dict[str, Any],
) -> None:
    request = component(schema, "AccountInputRequest")

    assert request["properties"]["name"]["maxLength"] == 30
    assert set(request["required"]) == {"kind", "name"}
    assert request["properties"]["kind"] == {
        "$ref": "#/components/schemas/AccountKindEnum"
    }
    assert component(schema, "AccountKindEnum")["enum"] == ["card", "cash", "other"]


def test_a_custom_category_is_written_with_a_name_and_an_icon_of_the_picker_only(
    schema: dict[str, Any],
) -> None:
    request = component(schema, "CategoryInputRequest")

    assert set(request["properties"]) == {"name", "icon"}  # no kind, no key
    assert request["properties"]["icon"]["allOf"] == [
        {"$ref": "#/components/schemas/IconEnum"}
    ]
    assert component(schema, "IconEnum")["enum"] == list(CUSTOM_ICONS)


def test_a_category_is_read_with_a_plain_icon_a_nullable_key_and_a_kind(
    schema: dict[str, Any],
) -> None:
    category = component(schema, "Category")["properties"]

    assert category["icon"]["type"] == "string"
    assert "enum" not in category["icon"]
    assert category["key"]["nullable"] is True
    assert set(category) == {
        "id",
        "name",
        "icon",
        "kind",
        "is_custom",
        "key",
        "version",
        "created_at",
        "updated_at",
    }


# --- transactions and the day totals ----------------------------------------------------------

TRANSACTIONS = "/api/v1/transactions/"
TRANSACTION = "/api/v1/transactions/{id}/"
DAY_TOTALS = "/api/v1/reports/day-totals/"


def test_the_transaction_and_report_operations_have_their_ids_and_tags(
    schema: dict[str, Any],
) -> None:
    found = {
        (path, method): (body["operationId"], body["tags"])
        for path in (TRANSACTIONS, TRANSACTION, DAY_TOTALS)
        for method, body in schema["paths"][path].items()
    }

    assert found == {
        (TRANSACTIONS, "get"): ("transaction_list", ["transactions"]),
        (TRANSACTIONS, "post"): ("transaction_create", ["transactions"]),
        (TRANSACTION, "get"): ("transaction_read", ["transactions"]),
        (TRANSACTION, "patch"): ("transaction_update", ["transactions"]),
        (TRANSACTION, "delete"): ("transaction_delete", ["transactions"]),
        (DAY_TOTALS, "get"): ("report_day_totals", ["reports"]),
    }


def test_the_transaction_create_needs_the_key_and_declares_409_and_422(
    schema: dict[str, Any],
) -> None:
    operation = op(schema, TRANSACTIONS, "post")

    assert params(operation)["Idempotency-Key"]["required"] is True
    assert {"201", "400", "401", "409", "422", "429"} <= operation["responses"].keys()
    assert "transaction_limit_reached" in operation["description"]


def test_a_change_and_a_delete_of_a_transaction_take_if_match_and_declare_412(
    schema: dict[str, Any],
) -> None:
    for method in ("patch", "delete"):
        operation = op(schema, TRANSACTION, method)

        assert "If-Match" in params(operation)
        assert "412" in operation["responses"]


def test_the_list_takes_the_filters_and_search_but_the_totals_do_not_take_search(
    schema: dict[str, Any],
) -> None:
    listed = params(op(schema, TRANSACTIONS, "get"))
    totals = params(op(schema, DAY_TOTALS, "get"))
    shared = {
        "date_from",
        "date_to",
        "category_id",
        "account_id",
        "kind",
        "limit",
        "cursor",
    }

    assert shared | {"search"} == set(listed)
    assert shared == set(totals)
    assert "search" not in totals
    assert listed["search"]["schema"]["minLength"] == 2
    assert listed["search"]["schema"]["maxLength"] == 100
    for name in ("category_id", "account_id"):
        assert listed[name]["schema"]["type"] == "array"
        assert listed[name]["schema"]["items"] == {"type": "string", "format": "uuid"}
        assert listed[name]["schema"]["maxItems"] == 20


def test_the_day_totals_are_the_paginated_envelope_of_days_with_their_currencies(
    schema: dict[str, Any],
) -> None:
    envelope = component(schema, "PaginatedDayTotalList")
    day = component(schema, "DayTotal")
    currency = component(schema, "CurrencyTotal")

    assert set(envelope["required"]) == {"next", "previous", "results"}
    assert envelope["properties"]["results"]["items"] == {
        "$ref": "#/components/schemas/DayTotal"
    }
    assert set(day["properties"]) == {"date", "totals"}
    assert day["properties"]["totals"]["items"] == {
        "$ref": "#/components/schemas/CurrencyTotal"
    }
    assert set(currency["properties"]) == {"currency", "total"}
    assert currency["properties"]["total"] == {
        "allOf": [{"$ref": "#/components/schemas/SignedAmount"}],
        "readOnly": True,
        "description": currency["properties"]["total"]["description"],
    }


def test_the_currency_is_written_on_create_and_not_on_a_change(
    schema: dict[str, Any],
) -> None:
    create = component(schema, "TransactionCreateRequest")
    change = component(schema, "PatchedTransactionUpdateRequest")

    assert "currency" in create["properties"]
    assert "currency" in create["required"]
    assert "currency" not in change["properties"]
    assert set(change["properties"]) == {
        "kind",
        "amount",
        "date",
        "category_id",
        "account_id",
        "note",
    }
    assert "required" not in change


def test_a_transaction_is_read_with_an_amount_and_ids(schema: dict[str, Any]) -> None:
    transaction = component(schema, "Transaction")["properties"]

    assert set(transaction) == {
        "id",
        "kind",
        "amount",
        "currency",
        "date",
        "category_id",
        "account_id",
        "note",
        "version",
        "created_at",
        "updated_at",
    }
    assert transaction["amount"]["allOf"] == [{"$ref": "#/components/schemas/Amount"}]
    assert transaction["category_id"]["format"] == "uuid"
    assert transaction["date"]["format"] == "date"
