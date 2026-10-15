"""The one error body of the API, as documented in the OpenAPI contract.

drf-standardized-errors produces the bodies at runtime; DRF_STANDARDIZED_ERRORS["ERROR_SCHEMAS"]
points here so every operation shares ONE 400 schema instead of a generated one per endpoint and
field. ``attr`` is the field the error belongs to (dotted for nested data) or null.
"""

from drf_spectacular.utils import OpenApiResponse
from drf_standardized_errors.openapi_serializers import (
    ClientErrorEnum,
    ValidationErrorEnum,
)
from rest_framework import serializers


class Error400Serializer(serializers.Serializer):  # type: ignore[misc]
    code = serializers.CharField()
    detail = serializers.CharField()
    attr = serializers.CharField(allow_null=True)


class Error400ResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    """``validation_error``: invalid input. ``client_error``: an unreadable request body."""

    type = serializers.ChoiceField(
        choices=[*ValidationErrorEnum.choices, *ClientErrorEnum.choices]
    )
    errors = Error400Serializer(many=True)


class Error409ResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    """``client_error``: a valid request that conflicts with the current state (``attr`` is null)."""

    type = serializers.ChoiceField(choices=ClientErrorEnum.choices)
    errors = Error400Serializer(many=True)


# 409 is documented per operation, not through ALLOWED_ERROR_STATUS_CODES, which would add it to every
# operation. An operation that can conflict says so:
#   @extend_schema(responses={204: None, 409: CONFLICT_RESPONSE})
CONFLICT_RESPONSE = OpenApiResponse(
    response=Error409ResponseSerializer,
    description="The request conflicts with the current state; the `code` says what conflicts.",
)
