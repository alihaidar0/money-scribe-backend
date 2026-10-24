"""What the OpenAPI document promises about the salary operations (ADR 0007, 0008, 0016)."""

from typing import Any

import pytest
from drf_spectacular.generators import SchemaGenerator

from common.money import AMOUNT_PATTERN, SIGNED_AMOUNT_PATTERN

BASE = "/api/v1/salary"
MONTH = f"{BASE}/months/{{month}}/"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    # drf-spectacular is only partly typed
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def op(schema: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["paths"][path][method]
    return found


def params(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in operation.get("parameters", [])}


def test_the_operations_have_the_ids_the_client_calls_them_by(
    schema: dict[str, Any],
) -> None:
    ids = {
        (path.removeprefix(BASE), method): body["operationId"]
        for path, methods in schema["paths"].items()
        if path.startswith(BASE)
        for method, body in methods.items()
    }

    assert ids == {
        ("/months/", "get"): "salary_month_list",
        ("/months/{month}/", "get"): "salary_month_read",
        ("/months/{month}/", "put"): "salary_month_put",
        ("/months/{month}/", "delete"): "salary_month_delete",
        ("/months/{month}/defaults/", "get"): "salary_month_defaults",
        ("/months/{month}/fixed-items/", "post"): "salary_fixed_item_create",
        ("/months/{month}/fixed-items/copy/", "post"): "salary_fixed_items_copy",
        ("/fixed-items/{id}/", "patch"): "salary_fixed_item_update",
        ("/fixed-items/{id}/", "delete"): "salary_fixed_item_delete",
    }


def test_every_operation_needs_the_bearer_token(schema: dict[str, Any]) -> None:
    for path, methods in schema["paths"].items():
        if path.startswith(BASE):
            for body in methods.values():
                assert body["security"] == [{"bearerAuth": []}], path


def test_a_month_is_a_path_parameter_with_its_format_described(
    schema: dict[str, Any],
) -> None:
    month = params(op(schema, MONTH, "get"))["month"]

    assert month["in"] == "path"
    assert "YYYY-MM" in month["description"]


def test_the_two_creates_require_the_idempotency_key_and_declare_409_and_422(
    schema: dict[str, Any],
) -> None:
    for path in (f"{MONTH}fixed-items/", f"{MONTH}fixed-items/copy/"):
        operation = op(schema, path, "post")

        assert params(operation)["Idempotency-Key"]["required"] is True
        assert {"409", "422"} <= operation["responses"].keys()


def test_saving_a_month_declares_201_200_409_412_and_the_etag(
    schema: dict[str, Any],
) -> None:
    operation = op(schema, MONTH, "put")

    assert {"200", "201", "409", "412"} <= operation["responses"].keys()
    assert params(operation)["If-Match"].get("required", False) is False
    assert "ETag" in operation["responses"]["201"]["headers"]


def test_a_change_and_a_delete_of_an_item_take_if_match_and_declare_412(
    schema: dict[str, Any],
) -> None:
    for method in ("patch", "delete"):
        operation = op(schema, f"{BASE}/fixed-items/{{id}}/", method)

        assert "If-Match" in params(operation)
        assert "412" in operation["responses"]


def test_deleting_a_month_declares_204_and_412(schema: dict[str, Any]) -> None:
    responses = op(schema, MONTH, "delete")["responses"]

    assert {"204", "412"} <= responses.keys()
    assert "content" not in responses["204"]


def test_the_history_is_the_paginated_envelope_with_limit_and_cursor(
    schema: dict[str, Any],
) -> None:
    operation = op(schema, f"{BASE}/months/", "get")
    envelope = schema["components"]["schemas"]["PaginatedSalaryMonthList"]

    assert {"limit", "cursor"} <= params(operation).keys()
    assert set(envelope["required"]) == {"next", "previous", "results"}
    assert envelope["properties"]["results"]["items"] == {
        "$ref": "#/components/schemas/SalaryMonth"
    }


def test_money_is_the_two_named_types(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]
    month = components["SalaryMonth"]["properties"]

    assert components["Amount"]["pattern"] == AMOUNT_PATTERN
    assert components["SignedAmount"]["pattern"] == SIGNED_AMOUNT_PATTERN
    assert month["base_salary"]["allOf"] == [{"$ref": "#/components/schemas/Amount"}]
    assert components["Balances"]["properties"]["first"]["allOf"] == [
        {"$ref": "#/components/schemas/SignedAmount"}
    ]
    assert components["Balances"]["properties"]["second"]["nullable"] is True
    assert components["FixedTotals"]["properties"]["second"]["nullable"] is True


def test_a_request_carries_the_inputs_only(schema: dict[str, Any]) -> None:
    request = schema["components"]["schemas"]["SalaryMonthInputRequest"]

    assert set(request["properties"]) == {
        "currency",
        "base_salary",
        "food_per_day",
        "working_days_in_month",
        "working_hours_in_month",
        "extra_hours_hundredths",
        "tax_rate_basis_points",
        "payment_mode",
        "working_days_first_half",
        "first_pay_day",
        "second_pay_day",
    }
    assert request["properties"]["working_hours_in_month"]["maximum"] == 744
    assert request["properties"]["extra_hours_hundredths"]["maximum"] == 74400


def test_the_copy_body_has_one_optional_field_named_from(
    schema: dict[str, Any],
) -> None:
    request = schema["components"]["schemas"]["CopyFixedItemsRequest"]

    assert list(request["properties"]) == ["from"]
    assert "required" not in request


def test_the_new_error_codes_are_in_the_registry_of_the_schema(
    schema: dict[str, Any],
) -> None:
    description = schema["components"]["schemas"]["ErrorItem"]["properties"]["code"][
        "description"
    ]

    assert "`fixed_item_limit_reached`" in description
    assert "`month_has_fixed_items`" in description
