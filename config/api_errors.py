"""The one error body of the API, as documented in the OpenAPI contract.

drf-standardized-errors produces the bodies at runtime; DRF_STANDARDIZED_ERRORS["ERROR_SCHEMAS"]
points here so every operation shares a few error schemas instead of a generated one per endpoint,
status and field. ``attr`` is the field the error belongs to (dotted for nested data) or null.

``code`` is a plain string, not an enum: a client must survive a code it does not know yet. The
registry (``config.error_codes.ErrorCode``, ADR 0004) is written into its description, so the
contract lists every code the server can send.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse
from drf_standardized_errors.openapi import AutoSchema
from drf_standardized_errors.openapi_serializers import (
    ClientErrorEnum,
    ServerErrorEnum,
    ValidationErrorEnum,
)
from rest_framework import serializers

from .error_codes import ErrorCode

# Codes that DRF and the validators raise on their own, next to the registry.
FRAMEWORK_CODES = (
    "required",
    "blank",
    "null",
    "invalid",
    "max_length",
    "min_length",
    "parse_error",
    "authentication_failed",
    "not_authenticated",
    "permission_denied",
    "not_found",
    "method_not_allowed",
    "not_acceptable",
    "unsupported_media_type",
    "read_only",
)


def _code_description() -> str:
    registry = ", ".join(f"`{code.value}`" for code in ErrorCode)
    framework = ", ".join(f"`{code}`" for code in FRAMEWORK_CODES)
    return (
        "A stable, machine-readable code; act on it, not on `detail`. "
        f"Registered codes: {registry}. "
        f"Codes raised by the framework for malformed or incomplete input: {framework}. "
        "A client treats a code it does not know as a generic failure of the status."
    )


class ErrorItemSerializer(serializers.Serializer):  # type: ignore[misc]
    code = serializers.CharField(help_text=_code_description())
    detail = serializers.CharField(help_text="Human-readable text; not for display.")
    attr = serializers.CharField(
        allow_null=True,
        help_text=(
            "The field the error belongs to, or null for the request as a whole. "
            "Sign-in sets it on `email_not_registered` (`email`) and `wrong_password` (`password`); "
            "`invalid_password` is on `current_password` (password change) or `password` (account deletion); "
            "`reset_token_invalid` is on `reset_token`."
        ),
    )
    attempts_left = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text="Only on `code_invalid`: how many wrong tries the email code still allows.",
    )
    min_length = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Only on `password_too_short`: the shortest password the server accepts.",
    )
    max_length = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Only on `password_too_long`: the longest password the server accepts.",
    )


class Error400ResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    """``validation_error``: invalid input. ``client_error``: an unreadable request body."""

    type = serializers.ChoiceField(
        choices=[*ValidationErrorEnum.choices, *ClientErrorEnum.choices]
    )
    errors = ErrorItemSerializer(many=True)


class ErrorResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    """``client_error``: a 401, 403, 404, 409 or 429 answer; ``Retry-After`` accompanies a 429."""

    type = serializers.ChoiceField(choices=ClientErrorEnum.choices)
    errors = ErrorItemSerializer(many=True)


class ServerErrorResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    """``server_error``: a 503 answer, always with ``Retry-After``; its one code is ``service_unavailable``."""

    type = serializers.ChoiceField(choices=ServerErrorEnum.choices)
    errors = ErrorItemSerializer(many=True)


# 409 is documented per operation, not through ALLOWED_ERROR_STATUS_CODES, which would add it to every
# operation. An operation that can conflict says so:
#   @extend_schema(responses={204: None, 409: CONFLICT_RESPONSE})
CONFLICT_RESPONSE = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The request conflicts with the current state; the `code` says what conflicts.",
)

# The limited calls (register, resend-code, verify-email, sign-in, the password reset calls, the password
# change and the account deletion) fail closed when the limit store is down (ADR 0006, 0014), so they
# declare it per operation. No other operation can answer a 503.
#   @extend_schema(parameters=[RETRY_AFTER_HEADER], responses={..., 503: SERVICE_UNAVAILABLE_RESPONSE})
SERVICE_UNAVAILABLE_RESPONSE = OpenApiResponse(
    response=ServerErrorResponseSerializer,
    description=(
        "A safety control (the rate-limit store) cannot run, so the request is refused before it "
        "changes anything; try again after `Retry-After`. The `code` is `service_unavailable`."
    ),
)

# The package documents the 429 of every operation but cannot describe its header; the operations that
# send one list this parameter (a `response` parameter is a response header, not a request one).
RETRY_AFTER_HEADER = OpenApiParameter(
    name="Retry-After",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.HEADER,
    response=[429, 503],
    description="Whole seconds to wait before the request may be repeated; at least 1.",
)

# For the operations that need no token (so the package would not document a 401 or 403 for them) but
# still answer one: sign-in (`email_not_registered`, `wrong_password`, `invalid_credentials`, `email_not_verified`), refresh (`session_*`).
UNAUTHORIZED_RESPONSE = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="Not authenticated, or the credentials, token or session are not valid; the `code` says which.",
)
FORBIDDEN_RESPONSE = OpenApiResponse(
    response=ErrorResponseSerializer,
    description="The credentials are right but the request is not allowed; the `code` says why.",
)


class ApiAutoSchema(AutoSchema):
    """The package's schema class without its canned error examples.

    Those examples show the package's own default codes (``authentication_failed``, ...), which this
    API does not send for a bad token or a failed sign-in; the registry above is the truth.
    """

    def _get_error_response_examples(self) -> list[OpenApiExample]:
        return []
