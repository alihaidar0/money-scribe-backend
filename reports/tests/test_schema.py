"""What the OpenAPI document promises about the reports and the Overview."""

from typing import Any

import pytest
from drf_spectacular.generators import SchemaGenerator

from common.money import Currency

MONTH = "/api/v1/reports/months/{month}/"
DEBTS = "/api/v1/reports/debts/"
OVERVIEW = "/api/v1/overview/months/{month}/"
AMOUNT = {"$ref": "#/components/schemas/Amount"}
SIGNED = {"$ref": "#/components/schemas/SignedAmount"}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def op(schema: dict[str, Any], path: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["paths"][path]["get"]
    return found


def component(schema: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["components"]["schemas"][name]
    return found


def test_the_operations_have_the_ids_and_tags_the_client_calls_them_by(
    schema: dict[str, Any],
) -> None:
    assert (op(schema, MONTH)["operationId"], op(schema, MONTH)["tags"]) == (
        "report_month",
        ["reports"],
    )
    assert (op(schema, DEBTS)["operationId"], op(schema, DEBTS)["tags"]) == (
        "report_debts",
        ["reports"],
    )
    assert (op(schema, OVERVIEW)["operationId"], op(schema, OVERVIEW)["tags"]) == (
        "overview_month",
        ["overview"],
    )


@pytest.mark.parametrize("path", [MONTH, DEBTS, OVERVIEW])
def test_each_report_is_a_get_with_the_bearer_token_and_the_usual_errors(
    schema: dict[str, Any], path: str
) -> None:
    assert list(schema["paths"][path]) == ["get"]
    operation = op(schema, path)

    assert operation["security"] == [{"bearerAuth": []}]
    assert operation["description"]
    assert {"200", "400", "401", "429"} <= operation["responses"].keys()
    assert "409" not in operation["responses"]


@pytest.mark.parametrize("path", [MONTH, OVERVIEW])
def test_the_month_reports_take_a_month_and_a_currency(
    schema: dict[str, Any], path: str
) -> None:
    found = {p["name"]: p for p in op(schema, path)["parameters"]}

    assert found["month"]["in"] == "path"
    assert found["currency"]["in"] == "query"
    assert found["currency"].get("required", False) is False
    assert found["currency"]["schema"]["enum"] == [c.value for c in Currency]


def test_the_debts_report_takes_an_optional_today(schema: dict[str, Any]) -> None:
    found = {p["name"]: p for p in op(schema, DEBTS)["parameters"]}

    assert set(found) == {"today"}
    assert found["today"]["schema"]["format"] == "date"


def test_the_overview_block_is_amounts_and_one_signed_balance(
    schema: dict[str, Any],
) -> None:
    block = component(schema, "OverviewBlock")

    assert set(block["required"]) == {
        "salary",
        "recurring_income",
        "fixed_costs",
        "income_transactions",
        "purchases",
        "debt_repayments_received",
        "debt_repayments_made",
        "balance",
    }
    for name in block["required"]:
        expected = SIGNED if name == "balance" else AMOUNT
        assert block["properties"][name]["allOf"] == [expected], name


def test_the_parts_are_nullable_blocks(schema: dict[str, Any]) -> None:
    overview = component(schema, "Overview")
    report = component(schema, "MonthReport")

    for body, name in ((overview, "OverviewBlock"), (report, "ReportSlice")):
        for part in ("first_part", "second_part"):
            field = body["properties"][part]
            assert field["nullable"] is True
            assert field["allOf"] == [{"$ref": f"#/components/schemas/{name}"}]
            assert part in body["required"]
    assert overview["properties"]["total"]["allOf"] == [
        {"$ref": "#/components/schemas/OverviewBlock"}
    ]


def test_the_month_report_has_every_field_of_the_contract(
    schema: dict[str, Any],
) -> None:
    report = component(schema, "MonthReport")

    assert set(report["required"]) == {
        "month",
        "currency",
        "has_data",
        "currencies",
        "income_transactions",
        "purchases",
        "categories",
        "first_part",
        "second_part",
    }
    assert "salary" in report["properties"]["income_transactions"]["description"]
    assert "fixed costs" in report["properties"]["purchases"]["description"]
    assert report["properties"]["categories"]["items"] == {
        "$ref": "#/components/schemas/CategorySpend"
    }


def test_a_category_spend_has_a_nullable_key(schema: dict[str, Any]) -> None:
    item = component(schema, "CategorySpend")

    assert set(item["required"]) == {
        "category_id",
        "category_key",
        "name",
        "icon",
        "amount",
    }
    assert item["properties"]["category_key"]["nullable"] is True
    assert item["properties"]["amount"]["allOf"] == [AMOUNT]


def test_the_debts_report_lists_overdue_and_open_debts_by_currency(
    schema: dict[str, Any],
) -> None:
    report = component(schema, "DebtsReport")
    block = component(schema, "CurrencyDebts")
    line = component(schema, "DebtLine")

    assert set(report["required"]) == {"has_data", "by_currency"}
    assert set(block["required"]) == {
        "currency",
        "owed_to_you",
        "you_owe",
        "overdue",
        "open",
    }
    assert set(line["required"]) == {
        "id",
        "person",
        "direction",
        "remaining",
        "status",
        "due_date",
    }
    assert line["properties"]["due_date"]["nullable"] is True
    assert line["properties"]["remaining"]["allOf"] == [AMOUNT]
