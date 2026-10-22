"""The OpenAPI document declares what the auth endpoints already do (headers, 503, sign-out 401s)."""

from typing import Any

import pytest
from drf_spectacular.generators import SchemaGenerator

AUTH = "/api/v1/auth"
LIMITED = [
    "register",
    "resend-code",
    "verify-email",
    "sign-in",
    "password-reset/request",
    "password-reset/verify",
    "password-reset/confirm",
    "password/change",
    "account/delete",
    "email-change",
    "email-change/confirm",
]
NEVER_503 = ["refresh", "sign-out", "profile"]


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    # drf-spectacular is only partly typed
    generator = SchemaGenerator()  # type: ignore[no-untyped-call]
    document: dict[str, Any] = generator.get_schema(request=None, public=True)  # type: ignore[no-untyped-call]
    return document


def _responses(schema: dict[str, Any], name: str) -> dict[str, Any]:
    path = schema["paths"][f"{AUTH}/{name}/"]
    responses: dict[str, Any] = next(iter(path.values()))["responses"]
    return responses


@pytest.mark.parametrize("name", LIMITED)
def test_a_limited_call_declares_retry_after_on_429_and_a_503(
    schema: dict[str, Any], name: str
) -> None:
    responses = _responses(schema, name)

    for status in ("429", "503"):
        header = responses[status]["headers"]["Retry-After"]
        assert header["schema"] == {"type": "integer"}
    assert responses["503"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ServerErrorResponse"
    }


def test_the_503_body_type_is_server_error(schema: dict[str, Any]) -> None:
    components = schema["components"]["schemas"]

    assert components["ServerErrorEnum"]["enum"] == ["server_error"]
    assert components["ServerErrorResponse"]["properties"]["type"] == {
        "$ref": "#/components/schemas/ServerErrorEnum"
    }
    assert components["ClientErrorEnum"]["enum"] == ["client_error"]


@pytest.mark.parametrize("name", NEVER_503)
def test_a_call_without_a_limit_declares_no_503(
    schema: dict[str, Any], name: str
) -> None:
    assert "503" not in _responses(schema, name)


PUBLIC_RESET = [
    "password-reset/request",
    "password-reset/verify",
    "password-reset/confirm",
]
SIGNED_IN = ["password/change", "account/delete"]


def _operation(schema: dict[str, Any], name: str) -> dict[str, Any]:
    operation: dict[str, Any] = next(iter(schema["paths"][f"{AUTH}/{name}/"].values()))
    return operation


@pytest.mark.parametrize("name", PUBLIC_RESET)
def test_the_reset_calls_need_no_token(schema: dict[str, Any], name: str) -> None:
    assert "security" not in _operation(schema, name)


@pytest.mark.parametrize("name", SIGNED_IN)
def test_the_password_change_and_the_deletion_need_the_bearer_token(
    schema: dict[str, Any], name: str
) -> None:
    assert _operation(schema, name)["security"] == [{"bearerAuth": []}]
    assert "401" in _responses(schema, name)


def test_the_new_operations_have_the_ids_the_client_calls_them_by(
    schema: dict[str, Any],
) -> None:
    ids = {
        name: _operation(schema, name)["operationId"]
        for name in PUBLIC_RESET + SIGNED_IN
    }

    assert ids == {
        "password-reset/request": "auth_password_reset_request",
        "password-reset/verify": "auth_password_reset_verify",
        "password-reset/confirm": "auth_password_reset_confirm",
        "password/change": "auth_password_change",
        "account/delete": "auth_account_delete",
    }


def test_the_deletion_and_the_confirmation_answer_204_without_a_body(
    schema: dict[str, Any],
) -> None:
    for name in ("password-reset/confirm", "account/delete"):
        assert "content" not in _responses(schema, name)["204"]
