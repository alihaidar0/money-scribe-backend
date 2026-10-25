"""What the OpenAPI document promises about debts and repayments (ADR 0004, 0008, 0016)."""

from typing import Any

import pytest
from drf_spectacular.generators import SchemaGenerator

DEBTS = "/api/v1/debts/"
DEBT = "/api/v1/debts/{id}/"
REPAYMENTS = "/api/v1/debts/{id}/repayments/"
REPAYMENT = "/api/v1/debts/{id}/repayments/{repayment_id}/"


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
        if path.startswith(DEBTS)
        for method, body in methods.items()
    }

    assert found == {
        (DEBTS, "get"): ("debt_list", ["debts"]),
        (DEBTS, "post"): ("debt_create", ["debts"]),
        (DEBT, "get"): ("debt_read", ["debts"]),
        (DEBT, "patch"): ("debt_update", ["debts"]),
        (DEBT, "delete"): ("debt_delete", ["debts"]),
        (REPAYMENTS, "post"): ("debt_repayment_create", ["debts"]),
        (REPAYMENT, "patch"): ("debt_repayment_update", ["debts"]),
        (REPAYMENT, "delete"): ("debt_repayment_delete", ["debts"]),
    }


def test_every_operation_needs_the_bearer_token_and_is_described(
    schema: dict[str, Any],
) -> None:
    for path in (DEBTS, DEBT, REPAYMENTS, REPAYMENT):
        for method, body in schema["paths"][path].items():
            assert body["security"] == [{"bearerAuth": []}], (path, method)
            assert body["description"], (path, method)
            assert {"400", "401", "429"} <= body["responses"].keys(), (path, method)


@pytest.mark.parametrize("path", [DEBTS, REPAYMENTS])
def test_the_creates_require_the_idempotency_key_and_declare_409_and_422(
    schema: dict[str, Any], path: str
) -> None:
    operation = op(schema, path, "post")

    assert params(operation)["Idempotency-Key"]["required"] is True
    assert {"201", "400", "401", "409", "422", "429"} <= operation["responses"].keys()
    assert "ETag" in operation["responses"]["201"]["headers"]
    assert "Idempotent-Replayed" in operation["responses"]["201"]["headers"]


@pytest.mark.parametrize(
    ("path", "methods"),
    [(DEBT, ("patch", "delete")), (REPAYMENT, ("patch", "delete"))],
)
def test_a_change_and_a_delete_take_if_match_and_declare_412(
    schema: dict[str, Any], path: str, methods: tuple[str, ...]
) -> None:
    for method in methods:
        operation = op(schema, path, method)

        assert params(operation)["If-Match"].get("required", False) is False
        assert {"412", "404"} <= operation["responses"].keys()


def test_every_answer_that_has_the_debt_as_its_body_says_so(
    schema: dict[str, Any],
) -> None:
    debt_ref = {"$ref": "#/components/schemas/Debt"}
    answers = [
        (DEBTS, "post", "201"),
        (DEBT, "get", "200"),
        (DEBT, "patch", "200"),
        (REPAYMENTS, "post", "201"),
        (REPAYMENT, "patch", "200"),
        (REPAYMENT, "delete", "200"),
    ]

    for path, method, status in answers:
        content = op(schema, path, method)["responses"][status]["content"]
        assert content["application/json"]["schema"] == debt_ref, (path, method)
    assert "ETag" in op(schema, REPAYMENT, "delete")["responses"]["200"]["headers"]
    assert "content" not in op(schema, DEBT, "delete")["responses"]["204"]


def test_the_repayment_calls_name_both_ids_in_the_path(schema: dict[str, Any]) -> None:
    for method in ("patch", "delete"):
        found = params(op(schema, REPAYMENT, method))

        assert found["id"]["in"] == found["repayment_id"]["in"] == "path"
        assert found["repayment_id"]["schema"]["format"] == "uuid"


def test_the_list_is_the_paginated_envelope_with_its_filters(
    schema: dict[str, Any],
) -> None:
    operation = op(schema, DEBTS, "get")
    found = params(operation)
    envelope = component(schema, "PaginatedDebtList")

    assert {"limit", "cursor", "direction", "status", "search", "today"} <= found.keys()
    assert found["status"]["schema"]["type"] == "array"
    assert found["status"]["schema"]["items"]["enum"] == ["open", "overdue", "settled"]
    assert (
        found["search"]["schema"]["minLength"],
        found["search"]["schema"]["maxLength"],
    ) == (2, 50)
    assert found["today"]["schema"]["format"] == "date"
    assert envelope["properties"]["results"]["items"] == {
        "$ref": "#/components/schemas/Debt"
    }
    assert "today" in params(op(schema, DEBT, "get"))


def test_a_debt_has_every_field_of_the_contract(schema: dict[str, Any]) -> None:
    debt = component(schema, "Debt")

    assert set(debt["required"]) == {
        "id",
        "direction",
        "person",
        "amount",
        "currency",
        "start_date",
        "due_date",
        "note",
        "repaid",
        "remaining",
        "status",
        "repayments",
        "version",
        "created_at",
        "updated_at",
    }
    for money in ("amount", "repaid", "remaining"):
        assert debt["properties"][money]["allOf"] == [
            {"$ref": "#/components/schemas/Amount"}
        ]
    assert debt["properties"]["due_date"]["nullable"] is True
    assert debt["properties"]["repayments"]["items"] == {
        "$ref": "#/components/schemas/Repayment"
    }
    assert component(schema, "Repayment")["required"] == [
        "amount",
        "created_at",
        "date",
        "id",
        "updated_at",
        "version",
    ]


def test_the_enums_are_named_after_the_debt(schema: dict[str, Any]) -> None:
    assert component(schema, "DebtDirectionEnum")["enum"] == ["lent", "borrowed"]
    assert component(schema, "DebtStatusEnum")["enum"] == ["open", "overdue", "settled"]


def test_the_create_asks_for_the_fields_of_the_contract_and_the_change_for_none(
    schema: dict[str, Any],
) -> None:
    create = component(schema, "DebtCreateRequest")
    change = component(schema, "PatchedDebtUpdateRequest")

    assert set(create["required"]) == {
        "direction",
        "person",
        "amount",
        "currency",
        "start_date",
    }
    assert set(create["properties"]) == {*create["required"], "due_date", "note"}
    assert create["properties"]["person"]["maxLength"] == 50
    assert create["properties"]["note"]["maxLength"] == 100
    assert create["properties"]["due_date"]["nullable"] is True
    assert set(change["properties"]) == {
        "person",
        "amount",
        "start_date",
        "due_date",
        "note",
    }
    assert "required" not in change
    assert "direction" not in change["properties"]
    assert "currency" not in change["properties"]


def test_a_repayment_is_written_with_an_amount_and_a_date_and_no_currency(
    schema: dict[str, Any],
) -> None:
    create = component(schema, "RepaymentInputRequest")
    change = component(schema, "PatchedRepaymentInputRequest")

    assert set(create["properties"]) == {"amount", "date"}
    assert set(create["required"]) == {"amount", "date"}
    assert set(change["properties"]) == {"amount", "date"}


def test_the_error_item_has_the_optional_remaining_amount(
    schema: dict[str, Any],
) -> None:
    item = component(schema, "ErrorItem")

    assert item["properties"]["remaining"]["allOf"] == [
        {"$ref": "#/components/schemas/Amount"}
    ]
    assert "remaining" not in item["required"]


@pytest.mark.parametrize(
    "code",
    [
        "amount_below_repaid",
        "due_before_start",
        "repayment_before_start",
        "start_after_repayment",
        "debt_limit_reached",
        "repayment_limit_reached",
        "exceeds_remaining",
    ],
)
def test_the_new_codes_are_in_the_description_of_the_error_code(
    schema: dict[str, Any], code: str
) -> None:
    assert (
        f"`{code}`"
        in component(schema, "ErrorItem")["properties"]["code"]["description"]
    )
