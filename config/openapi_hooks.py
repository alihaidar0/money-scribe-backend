"""Document the headers that responses of the API carry (ADR 0021)."""

from typing import Any, Final

_METHODS: Final = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
_REQUEST_ID_COMPONENT: Final = "XRequestId"
_REQUEST_ID: dict[str, Any] = {
    "description": (
        "The id of this request, on every response, errors included. A client may send its own `X-Request-ID` "
        "(8 to 64 letters, digits and hyphens); anything else is replaced. It holds no personal data: show it on "
        "a failure screen so the request can be found in the server log."
    ),
    "schema": {"type": "string", "pattern": "^[A-Za-z0-9-]{8,64}$"},
}

_RETRY_AFTER: dict[str, Any] = {
    "description": "Whole seconds to wait before the request may be repeated; at least 1.",
    "schema": {"type": "integer"},
}


def document_common_headers(
    result: dict[str, Any], generator: Any, request: Any, public: bool
) -> dict[str, Any]:
    """The drf-spectacular post-processing hook for the headers every response carries.

    ``X-Request-ID`` goes on every response of every operation. ``Retry-After`` goes on every ``429`` and ``503``
    that does not declare it already: any signed-in operation can now answer a ``429`` (the per-user limits), and the
    operations that always could declare the header themselves.
    """
    reference = {"$ref": f"#/components/headers/{_REQUEST_ID_COMPONENT}"}
    for item in result.get("paths", {}).values():
        for method, operation in item.items():
            if method not in _METHODS or not isinstance(operation, dict):
                continue
            for status, response in operation.get("responses", {}).items():
                headers = response.setdefault("headers", {})
                headers["X-Request-ID"] = reference
                if status in {"429", "503"}:
                    headers.setdefault("Retry-After", dict(_RETRY_AFTER))
    result.setdefault("components", {}).setdefault("headers", {})[
        _REQUEST_ID_COMPONENT
    ] = _REQUEST_ID
    return result
