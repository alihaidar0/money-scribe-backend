from rest_framework import status
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    ErrorDetail,
    Throttled,
    ValidationError,
)

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


class FieldAuthenticationFailed(AuthenticationFailed):  # type: ignore[misc]
    """A failed sign-in that names the field at fault (HTTP 401, ``attr`` ``email`` or ``password``).

    The error formatter copies ``attr`` into the error item (``config.error_format``), so the client
    can show the message under the right field.
    """

    def __init__(self, code: ErrorCode, attr: str, detail: str) -> None:
        self.attr = attr
        super().__init__(detail=detail, code=code.value)


class CodeInvalid(ValidationError):  # type: ignore[misc]
    """The 6-digit email code is wrong (HTTP 400, field ``code``).

    ``attempts_left`` is how many wrong tries the code still allows. The exception handler adds it to
    the ``code_invalid`` item of the error body (``config.exception_handler``).
    """

    def __init__(self, attempts_left: int) -> None:
        self.attempts_left = attempts_left
        super().__init__(
            {
                "code": [
                    ErrorDetail(
                        "The code is not correct.", code=ErrorCode.CODE_INVALID.value
                    )
                ]
            }
        )


class ServiceUnavailable(APIException):  # type: ignore[misc]
    """A safety control cannot run, so the request is refused rather than let through (HTTP 503).

    The sign-in lock and the code limits live in Redis. If it is down, accepting the request would
    switch off the brake against password and code guessing, so those calls fail closed. The error
    handler sends ``wait`` as ``Retry-After``.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The service is temporarily unavailable. Try again shortly."
    default_code = ErrorCode.SERVICE_UNAVAILABLE.value

    def __init__(self, wait: int = 30) -> None:
        super().__init__()
        self.wait = wait


def service_unavailable(wait: int = 30) -> ServiceUnavailable:
    """Build the ``503`` that a failed safety control answers with."""
    return ServiceUnavailable(wait=wait)
