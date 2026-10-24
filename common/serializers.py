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

import re
from typing import Any, ClassVar

from drf_spectacular.extensions import OpenApiSerializerFieldExtension
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


class CurrencyField(serializers.ChoiceField):  # type: ignore[misc]
    """The ISO 4217 code of a supported currency."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("choices", [(c.value, c.value) for c in Currency])
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
