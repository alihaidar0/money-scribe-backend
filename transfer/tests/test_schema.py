"""What the OpenAPI document promises about the export and the import."""

from typing import Any

import pytest
from drf_spectacular.generators import SchemaGenerator

EXPORTS = "/api/v1/exports/"
IMPORTS = "/api/v1/imports/"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def op(schema: dict[str, Any], path: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["paths"][path]["post"]
    return found


def component(schema: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["components"]["schemas"][name]
    return found


def test_the_operations_have_the_ids_and_tags_the_client_calls_them_by(
    schema: dict[str, Any],
) -> None:
    assert (op(schema, EXPORTS)["operationId"], op(schema, EXPORTS)["tags"]) == (
        "export_create",
        ["exports"],
    )
    assert (op(schema, IMPORTS)["operationId"], op(schema, IMPORTS)["tags"]) == (
        "import_create",
        ["imports"],
    )
    assert list(schema["paths"][EXPORTS]) == ["post"]
    assert list(schema["paths"][IMPORTS]) == ["post"]


@pytest.mark.parametrize("path", [EXPORTS, IMPORTS])
def test_each_operation_needs_the_bearer_token_declares_the_503_and_is_described(
    schema: dict[str, Any], path: str
) -> None:
    operation = op(schema, path)

    assert operation["security"] == [{"bearerAuth": []}]
    assert operation["description"]
    assert {"200", "400", "401", "429", "503"} <= operation["responses"].keys()
    assert "Retry-After" in operation["responses"]["503"]["headers"]
    assert "Retry-After" in operation["responses"]["429"]["headers"]


def test_the_export_answers_a_csv_or_a_zip_as_binary_with_its_headers(
    schema: dict[str, Any],
) -> None:
    answer = op(schema, EXPORTS)["responses"]["200"]

    assert set(answer["content"]) == {"text/csv", "application/zip"}
    for media in answer["content"].values():
        assert media["schema"] == {"type": "string", "format": "binary"}
    assert "Content-Disposition" in answer["headers"]


def test_the_export_asks_for_kinds_a_password_and_an_optional_period(
    schema: dict[str, Any],
) -> None:
    request = component(schema, "ExportRequestRequest")

    assert set(request["required"]) == {"kinds", "current_password"}
    assert set(request["properties"]) == {"kinds", "current_password", "from", "to"}
    kinds = request["properties"]["kinds"]
    assert (kinds["minItems"], kinds["maxItems"], kinds["uniqueItems"]) == (1, 3, True)
    # The same three values as the datasets of the import: one shared component.
    assert kinds["items"] == {"$ref": "#/components/schemas/DatasetEnum"}
    assert request["properties"]["from"]["format"] == "date"
    assert request["properties"]["current_password"]["writeOnly"] is True


def test_the_key_is_declared_but_not_required_because_a_dry_run_needs_none(
    schema: dict[str, Any],
) -> None:
    operation = op(schema, IMPORTS)
    key = next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")

    assert key.get("required", False) is False
    assert "dry_run" in key["description"]
    assert "Idempotent-Replayed" in operation["responses"]["200"]["headers"]
    assert "422" in operation["responses"]


def test_the_import_takes_one_to_500_rows_each_with_a_row_number(
    schema: dict[str, Any],
) -> None:
    request = component(schema, "ImportRequestRequest")
    rows = request["properties"]["rows"]

    assert set(request["required"]) == {"dataset", "rows"}
    assert (rows["minItems"], rows["maxItems"]) == (1, 500)
    assert rows["items"]["required"] == ["row"]
    assert rows["items"]["properties"]["row"]["minimum"] == 1
    assert request["properties"]["dry_run"]["default"] is False
    assert component(schema, "DatasetEnum")["enum"] == [
        "transactions",
        "debts",
        "salary_months",
    ]


def test_the_answer_lists_one_problem_per_skipped_row_with_a_plain_string_code(
    schema: dict[str, Any],
) -> None:
    result = component(schema, "ImportResult")
    problem = component(schema, "ImportProblem")

    assert set(result["required"]) == {
        "dataset",
        "dry_run",
        "imported",
        "skipped",
        "problems",
    }
    assert result["properties"]["problems"]["items"] == {
        "$ref": "#/components/schemas/ImportProblem"
    }
    assert set(problem["required"]) == {"row", "code", "attr"}
    assert problem["properties"]["code"]["type"] == "string"
    assert "enum" not in problem["properties"]["code"]
    assert problem["properties"]["attr"]["nullable"] is True


@pytest.mark.parametrize("code", ["unknown_category", "unknown_account", "duplicate"])
def test_the_new_codes_are_in_the_description_of_the_error_code(
    schema: dict[str, Any], code: str
) -> None:
    assert (
        f"`{code}`"
        in component(schema, "ErrorItem")["properties"]["code"]["description"]
    )
