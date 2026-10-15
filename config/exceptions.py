from rest_framework import status
from rest_framework.exceptions import APIException, Throttled

from .error_codes import ErrorCode


class Conflict(APIException):  # type: ignore[misc]
    """A valid request that conflicts with the current state of the resource (HTTP 409).

    Not a field error: the body's ``attr`` is ``null`` and the ``code`` says what conflicts,
    for example ``account_has_transactions``. Raise it with :func:`conflict`.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state of the resource."
    default_code = "conflict"


def conflict(code: ErrorCode, detail: str | None = None) -> Conflict:
    """Build a 409 error carrying one of the registered codes."""
    return Conflict(detail=detail, code=code.value)


def throttled(code: ErrorCode, wait: int, detail: str | None = None) -> Throttled:
    """Build a 429 error that tells the client how long to wait.

    The error handler sends ``wait`` as the ``Retry-After`` header, in whole seconds (ADR 0004).
    """
    return Throttled(wait=wait, detail=detail, code=code.value)
