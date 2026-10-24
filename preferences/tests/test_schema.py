from typing import Any

import pytest
from drf_spectacular.generators import SchemaGenerator

PATH = "/api/v1/settings/"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def test_the_two_operations_and_their_ids(schema: dict[str, Any]) -> None:
    assert {m: o["operationId"] for m, o in schema["paths"][PATH].items()} == {
        "get": "settings_read",
        "patch": "settings_update",
    }


def test_both_need_the_bearer_and_the_change_takes_if_match_and_declares_412(
    schema: dict[str, Any],
) -> None:
    for operation in schema["paths"][PATH].values():
        assert operation["security"] == [{"bearerAuth": []}]
    patch = schema["paths"][PATH]["patch"]
    assert "If-Match" in {p["name"] for p in patch["parameters"]}
    assert {"200", "400", "412"} <= patch["responses"].keys()
    assert "ETag" in patch["responses"]["200"]["headers"]


def test_the_request_has_only_the_settings_and_the_nullable_ones_say_so(
    schema: dict[str, Any],
) -> None:
    request = schema["components"]["schemas"]["PatchedSettingsUpdateRequest"]

    assert set(request["properties"]) == {
        "language",
        "time_zone",
        "default_currency",
        "payment_mode",
        "first_pay_day",
        "second_pay_day",
        "remind_on_pay_days",
    }
    for field in (
        "default_currency",
        "payment_mode",
        "first_pay_day",
        "second_pay_day",
    ):
        assert request["properties"][field]["nullable"] is True
    for field in ("language", "time_zone", "remind_on_pay_days"):
        assert request["properties"][field].get("nullable", False) is False
    assert request["properties"]["first_pay_day"]["minimum"] == 1
    assert request["properties"]["first_pay_day"]["maximum"] == 28


def test_the_answer_always_has_every_field(schema: dict[str, Any]) -> None:
    answer = schema["components"]["schemas"]["Settings"]

    assert set(answer["required"]) == {
        "language",
        "time_zone",
        "default_currency",
        "payment_mode",
        "first_pay_day",
        "second_pay_day",
        "remind_on_pay_days",
        "version",
        "updated_at",
    }
