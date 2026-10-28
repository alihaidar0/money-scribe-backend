"""Money on the wire for serializers (ADR 0008): the shared ``Amount`` and ``SignedAmount`` fields.

An amount is a string next to a ``currency`` field, so a field alone cannot read it: the digits depend on the
sibling. The fields therefore check the *shape* (a string, never a JSON number), and :class:`MoneySerializerMixin`
turns every amount into a :class:`~common.money.Money` once the currency is known, with ``400 invalid_amount`` on
the field that is wrong. Output goes the other way: give the field a ``Money`` and it writes the exact string.

    class FixedItemSerializer(MoneySerializerMixin, serializers.ModelSerializer):
        amount = AmountField(allow_zero=False)
        currency = CurrencyField()

This is the one place the API reads or writes an amount; the OpenAPI schema names the two types ``Amount`` and
``SignedAmount`` (``common/openapi.py``).
"""

import datetime
import re
from typing import Any, ClassVar

from drf_spectacular.extensions import OpenApiSerializerFieldExtension
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, ValidationError

from config.error_codes import ErrorCode

from .money import (
    AMOUNT_PATTERN,
    SIGNED_AMOUNT_PATTERN,
    Currency,
    InvalidAmount,
    Money,
)
from .text import has_forbidden_character, normalize_text

_MONTH = re.compile(r"(20[0-9]{2})-(0[1-9]|1[0-2])")


def _invalid(message: str) -> ValidationError:
    return ValidationError(message, code=ErrorCode.INVALID_AMOUNT.value)


class AmountField(serializers.Field):  # type: ignore[misc]
    """A stored or input amount: a decimal string, never a number, and not negative.

    ``allow_zero=False`` is for the amounts that must be greater than zero (a transaction, a repayment, a debt, a
    fixed item); a food allowance may be zero.
    """

    signed = False
    pattern = AMOUNT_PATTERN
    default_error_messages: ClassVar[dict[str, str]] = {
        "invalid": "An amount is a decimal string."
    }

    def __init__(self, *, allow_zero: bool = True, **kwargs: Any) -> None:
        self.allow_zero = allow_zero
        kwargs.setdefault(
            "help_text",
            "A decimal string with exactly the number of digits of the currency, such as `12.50`.",
        )
        super().__init__(**kwargs)
        self._shape = re.compile(self.pattern)

    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str) or self._shape.fullmatch(data) is None:
            raise _invalid("An amount is a decimal string.")
        return data

    def to_representation(self, value: Any) -> str:
        if not isinstance(value, Money):
            raise TypeError("Give an amount field a Money.")
        return value.to_string()


class SignedAmountField(AmountField):
    """A computed amount that can be negative (a balance, a day total); ``-0`` is never written."""

    signed = True
    pattern = SIGNED_AMOUNT_PATTERN


# The supported currencies as choices. The schema names this set once, ``CurrencyEnum``, whatever the field is
# called (``currency``, ``default_currency``): see ``ENUM_NAME_OVERRIDES`` in the settings.
CURRENCY_CHOICES = [(c.value, c.value) for c in Currency]


class CurrencyField(serializers.ChoiceField):  # type: ignore[misc]
    """The ISO 4217 code of a supported currency."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("choices", CURRENCY_CHOICES)
        kwargs.setdefault("help_text", "The ISO 4217 code of the currency.")
        super().__init__(**kwargs)


class MoneySerializerMixin(serializers.Serializer):  # type: ignore[misc]
    """Turns the amount fields into ``Money`` once the currency is known.

    Put it before the serializer base. The currency is read from ``currency_field`` of the input, or, on a partial
    update that leaves it out, from the instance. An amount that does not fit the currency is
    ``400 invalid_amount`` on that field.
    """

    currency_field = "currency"

    def _currency_of(self, attrs: dict[str, Any]) -> Currency | None:
        value = attrs.get(self.currency_field)
        if value is None and self.instance is not None:
            value = getattr(self.instance, self.currency_field, None)
        return None if value is None else Currency(str(value))

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        amounts = {
            name: field
            for name, field in self.fields.items()
            if isinstance(field, AmountField) and name in attrs
        }
        if not amounts:
            return attrs
        currency = self._currency_of(attrs)
        if currency is None:
            raise ValidationError(
                {
                    self.currency_field: [
                        ErrorDetail("This field is required.", code="required")
                    ]
                }
            )
        errors: dict[str, list[ErrorDetail]] = {}
        for name, field in amounts.items():
            try:
                attrs[name] = Money.parse(
                    attrs[name],
                    currency,
                    signed=field.signed,
                    allow_zero=field.allow_zero,
                )
            except InvalidAmount as error:
                errors[name] = [
                    ErrorDetail(str(error), code=ErrorCode.INVALID_AMOUNT.value)
                ]
        if errors:
            raise ValidationError(errors)
        return attrs


@extend_schema_field(
    {"type": "string", "pattern": r"^20[0-9]{2}-(0[1-9]|1[0-2])$", "example": "2026-10"}
)
class MonthField(serializers.Field):  # type: ignore[misc]
    """A month as ``YYYY-MM`` (years 2000 to 2099); in the code it is the first day of that month."""

    default_error_messages: ClassVar[dict[str, str]] = {
        "invalid": "A month as YYYY-MM, from 2000-01 to 2099-12."
    }

    def to_internal_value(self, data: Any) -> datetime.date:
        match = _MONTH.fullmatch(data) if isinstance(data, str) else None
        if match is None:
            raise self.fail("invalid")
        return datetime.date(int(match.group(1)), int(match.group(2)), 1)

    def to_representation(self, value: datetime.date) -> str:
        return f"{value:%Y-%m}"


class TextField(serializers.CharField):  # type: ignore[misc]
    """A user-entered text value (a name, a note) under the rules of ADR 0021.

    Only a string is accepted (a number is not turned into text). The value is trimmed and normalized to NFC, the
    length limit counts code points after that, and a control character, a line separator or a bidirectional
    override, embedding or isolate character is ``400 invalid_characters`` on the field.
    """

    default_error_messages: ClassVar[dict[str, str]] = {
        ErrorCode.INVALID_CHARACTERS.value: "This field contains characters that are not allowed."
    }

    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        value = normalize_text(super().to_internal_value(data))
        if has_forbidden_character(value):
            self.fail(ErrorCode.INVALID_CHARACTERS.value)
        return value


class WholeNumberField(serializers.IntegerField):  # type: ignore[misc]
    """A JSON integer. A string, a decimal and a boolean are refused, so ``"20"`` is not a number of days."""

    def to_internal_value(self, data: Any) -> int:
        if type(data) is not int:
            self.fail("invalid")
        value: int = super().to_internal_value(data)
        return value


class StrictBooleanField(serializers.BooleanField):  # type: ignore[misc]
    """A JSON ``true`` or ``false``; ``"true"``, ``1`` and ``null`` are refused."""

    def to_internal_value(self, data: Any) -> bool:
        if type(data) is not bool:
            self.fail("invalid")
        return bool(data)


class RefuseUnknownFieldsMixin(serializers.Serializer):  # type: ignore[misc]
    """A field that the serializer does not have is ``400 read_only`` on that field, never silently ignored.

    For the edit of a record that has fields the client may not set (a version, a result, an id): a client that
    sends one learns at once that it was not applied.
    """

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            refused = {
                field: [
                    ErrorDetail("This field cannot be changed here.", code="read_only")
                ]
                for field in data
                if field not in self.fields
            }
            if refused:
                raise ValidationError(refused)
        value: dict[str, Any] = super().to_internal_value(data)
        return value


def currency_mismatch(field: str = "currency") -> ValidationError:
    """The ``400 currency_mismatch`` for a request that carries another currency than the record it belongs to."""
    return ValidationError(
        {
            field: [
                ErrorDetail(
                    "The currency does not match.",
                    code=ErrorCode.CURRENCY_MISMATCH.value,
                )
            ]
        }
    )


class AmountFieldExtension(OpenApiSerializerFieldExtension):  # type: ignore[no-untyped-call]
    """Describe an amount field as a string with the pattern of ADR 0008; the hook names the type."""

    target_class = "common.serializers.AmountField"
    match_subclasses = True

    def map_serializer_field(self, auto_schema: Any, direction: str) -> dict[str, Any]:
        field = self.target
        return {
            "type": "string",
            "format": "decimal",
            "pattern": field.pattern,
            "x-money-type": "SignedAmount" if field.signed else "Amount",
        }
