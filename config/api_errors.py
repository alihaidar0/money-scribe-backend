"""The one error body of the API, as documented in the OpenAPI contract.

drf-standardized-errors produces the bodies at runtime; DRF_STANDARDIZED_ERRORS["ERROR_SCHEMAS"]
points here so every operation shares ONE 400 schema instead of a generated one per endpoint and
field. ``attr`` is the field the error belongs to (dotted for nested data) or null.
"""

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
