"""What the OpenAPI document and the settings promise about the safe writes (ADR 0016)."""

from typing import Any

import pytest
from django.conf import settings
from drf_spectacular.generators import SchemaGenerator

from config.error_codes import ErrorCode
from idempotency.tests import support


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    generator = SchemaGenerator(patterns=support.api_patterns)  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def operation(schema: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    found: dict[str, Any] = schema["paths"][path][method]
    return found


def parameters(op: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in op.get("parameters", [])}


def test_a_create_requires_the_key_and_documents_replay_conflict_and_reuse(
    schema: dict[str, Any],
) -> None:
    op = operation(schema, "/notes/", "post")

    assert parameters(op)["Idempotency-Key"]["required"] is True
    assert parameters(op)["Idempotency-Key"]["in"] == "header"
    assert {"201", "400", "401", "409", "422"} <= op["responses"].keys()
    headers = op["responses"]["201"]["headers"]
    assert {"Idempotent-Replayed", "ETag"} <= headers.keys()


def test_an_edit_may_send_if_match_and_documents_the_412(
    schema: dict[str, Any],
) -> None:
    for method in ("patch", "put"):
        op = operation(schema, "/notes/{id}/", method)

        assert parameters(op)["If-Match"].get("required", False) is False
        assert "412" in op["responses"]
        assert "ETag" in op["responses"]["200"]["headers"]


def test_a_delete_may_send_if_match_and_documents_the_412(
    schema: dict[str, Any],
) -> None:
    op = operation(schema, "/notes/{id}/", "delete")

    assert parameters(op)["If-Match"].get("required", False) is False
    assert "412" in op["responses"]


def test_the_412_and_422_share_the_one_error_body(schema: dict[str, Any]) -> None:
    create = operation(schema, "/notes/", "post")["responses"]
    edit = operation(schema, "/notes/{id}/", "patch")["responses"]

    for response in (create["422"], edit["412"], create["409"]):
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_the_registry_in_the_schema_lists_the_five_codes(
    schema: dict[str, Any],
) -> None:
    description = schema["components"]["schemas"]["ErrorItem"]["properties"]["code"][
        "description"
    ]

    for code in (
        "idempotency_key_required",
        "idempotency_key_invalid",
        "idempotency_key_reused",
        "idempotency_in_progress",
        "precondition_failed",
    ):
        assert f"`{code}`" in description
        assert ErrorCode(code)


def test_the_browser_can_send_and_read_the_headers_of_the_safe_writes() -> None:
    allowed = {h.lower() for h in settings.CORS_ALLOW_HEADERS}
    exposed = {h.lower() for h in settings.CORS_EXPOSE_HEADERS}

    assert {"idempotency-key", "if-match", "if-none-match"} <= allowed
    assert {"authorization", "content-type"} <= allowed  # the defaults are kept
    assert {"etag", "idempotent-replayed", "retry-after"} <= exposed


def test_the_limits_of_the_adr_are_the_settings() -> None:
    assert settings.IDEMPOTENCY_KEY_LIFETIME_SECONDS == 24 * 60 * 60
    assert settings.IDEMPOTENCY_LOCK_WAIT_SECONDS == 3
