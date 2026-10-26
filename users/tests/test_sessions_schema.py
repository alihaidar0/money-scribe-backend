"""What the OpenAPI document promises about the sessions and the security activity."""

from typing import Any

import pytest
from drf_spectacular.generators import SchemaGenerator

SESSIONS = "/api/v1/auth/sessions/"
SESSION = "/api/v1/auth/sessions/{id}/"
REVOKE = "/api/v1/auth/sessions/revoke-others/"
HISTORY = "/api/v1/auth/session-history/"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def op(schema: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["paths"][path][method]
    return found


def component(schema: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["components"]["schemas"][name]
    return found


def test_the_operations_have_the_ids_and_tags_the_client_calls_them_by(
    schema: dict[str, Any],
) -> None:
    found = {
        (path, method): (body["operationId"], body["tags"])
        for path in (SESSIONS, SESSION, REVOKE, HISTORY)
        for method, body in schema["paths"][path].items()
    }

    assert found == {
        (SESSIONS, "get"): ("auth_session_list", ["sessions"]),
        (SESSION, "delete"): ("auth_session_end", ["sessions"]),
        (REVOKE, "post"): ("auth_session_revoke_others", ["sessions"]),
        (HISTORY, "get"): ("auth_session_history", ["sessions"]),
    }


@pytest.mark.parametrize(
    ("path", "method"),
    [(SESSIONS, "get"), (SESSION, "delete"), (REVOKE, "post"), (HISTORY, "get")],
)
def test_every_operation_needs_the_bearer_token_and_is_described(
    schema: dict[str, Any], path: str, method: str
) -> None:
    operation = op(schema, path, method)

    assert operation["security"] == [{"bearerAuth": []}]
    assert operation["description"]
    assert {"401", "429"} <= operation["responses"].keys()


def test_only_the_bulk_call_declares_the_503(schema: dict[str, Any]) -> None:
    assert "503" in op(schema, REVOKE, "post")["responses"]
    for path, method in ((SESSIONS, "get"), (SESSION, "delete"), (HISTORY, "get")):
        assert "503" not in op(schema, path, method)["responses"]


def test_ending_one_is_204_with_no_body_and_a_404(schema: dict[str, Any]) -> None:
    responses = op(schema, SESSION, "delete")["responses"]

    assert {"204", "404"} <= responses.keys()
    assert "content" not in responses["204"]
    id_parameter = next(
        p for p in op(schema, SESSION, "delete")["parameters"] if p["name"] == "id"
    )
    assert (id_parameter["in"], id_parameter["schema"]["format"]) == ("path", "uuid")


def test_the_session_list_takes_the_status_and_the_cursor_parameters(
    schema: dict[str, Any],
) -> None:
    found = {p["name"]: p for p in op(schema, SESSIONS, "get")["parameters"]}

    assert {"status", "limit", "cursor"} <= found.keys()
    assert found["status"].get("required", False) is False
    assert found["status"]["schema"]["enum"] == ["active", "ended"]
    assert found["status"]["schema"]["default"] == "active"
    assert "PaginatedSessionList" in schema["components"]["schemas"]


def test_a_session_has_every_field_of_the_contract_with_a_nullable_end(
    schema: dict[str, Any],
) -> None:
    session = component(schema, "Session")

    assert set(session["required"]) == {
        "id",
        "device_id",
        "device_name",
        "platform",
        "os_version",
        "app_version",
        "signed_in_at",
        "last_active_at",
        "expires_at",
        "is_current",
        "sign_in_method",
        "network",
        "ended_at",
        "end_reason",
    }
    for name in ("ended_at", "end_reason", "network"):
        assert session["properties"][name]["nullable"] is True
    assert session["properties"]["end_reason"]["type"] == "string"
    assert "enum" not in session["properties"]["end_reason"]
    for reason in (
        "signed_out",
        "revoked_by_user",
        "expired",
        "session_limit",
        "replaced",
        "password_reset",
    ):
        assert f"`{reason}`" in session["properties"]["end_reason"]["description"]
    assert (
        "user_agent" not in session["properties"] and "ip" not in session["properties"]
    )


def test_the_revoke_call_takes_the_password_and_answers_two_counts(
    schema: dict[str, Any],
) -> None:
    request = component(schema, "RevokeOthersRequest")
    answer = component(schema, "RevokeOthersResult")

    assert request["required"] == ["current_password"]
    assert list(request["properties"]) == ["current_password"]
    assert request["properties"]["current_password"]["writeOnly"] is True
    assert set(answer["required"]) == {"ended", "device_keys_removed"}


def test_an_entry_of_the_security_activity_has_nullable_device_fields(
    schema: dict[str, Any],
) -> None:
    entry = component(schema, "SecurityEvent")

    assert set(entry["required"]) == {
        "id",
        "type",
        "occurred_at",
        "device_name",
        "platform",
        "app_version",
        "sign_in_method",
        "session_id",
        "kinds",
        "count",
    }
    for name in (
        "device_name",
        "platform",
        "app_version",
        "sign_in_method",
        "session_id",
        "kinds",
        "count",
    ):
        assert entry["properties"][name]["nullable"] is True
    assert component(schema, "SecurityEventTypeEnum")["enum"] == [
        "signed_in",
        "signed_out",
        "session_revoked",
        "session_limit_reached",
        "password_changed",
        "password_reset",
        "email_changed",
        "device_key_added",
        "device_key_removed",
        "token_reuse_detected",
        "data_exported",
        "data_imported",
    ]
    assert component(schema, "SignInMethodEnum")["enum"] == [
        "password",
        "email_code",
        "device_key",
    ]
    assert "PaginatedSecurityEventList" in schema["components"]["schemas"]


def test_the_new_code_is_in_the_description_of_the_error_code(
    schema: dict[str, Any],
) -> None:
    description = component(schema, "ErrorItem")["properties"]["code"]["description"]

    assert "`session_limit_reached`" in description
