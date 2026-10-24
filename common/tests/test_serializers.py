"""The shared amount fields: shape on the way in, ``Money`` once the currency is known, the exact string out."""

from types import SimpleNamespace
from typing import Any

import pytest
from rest_framework import serializers

from common.money import AMOUNT_PATTERN, SIGNED_AMOUNT_PATTERN, Currency, Money
from common.serializers import (
    AmountField,
    CurrencyField,
    MoneySerializerMixin,
    SignedAmountField,
    currency_mismatch,
)


class ItemSerializer(MoneySerializerMixin, serializers.Serializer):  # type: ignore[misc]
    amount = AmountField(allow_zero=False)
    allowance = AmountField()
    currency = CurrencyField()


class BalanceSerializer(serializers.Serializer):  # type: ignore[misc]
    balance = SignedAmountField()
    currency = CurrencyField()


def codes(serializer: serializers.Serializer) -> dict[str, list[str]]:
    return {
        field: [error.code for error in errors]
        for field, errors in serializer.errors.items()
    }


def valid(**overrides: Any) -> dict[str, Any]:
    """A good body; the allowance is a zero with the digits of whichever currency the test picks."""
    body: dict[str, Any] = {
        "amount": "12.50",
        "allowance": None,
        "currency": "USD",
        **overrides,
    }
    if body["allowance"] is None:
        known = body["currency"] in {c.value for c in Currency}
        body["allowance"] = (
            Money.zero(Currency(body["currency"])).to_string() if known else "0.00"
        )
    return body


def test_a_valid_body_becomes_money_of_the_currency() -> None:
    serializer = ItemSerializer(data=valid())

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["amount"] == Money(1250, Currency.USD)
    assert serializer.validated_data["allowance"] == Money.zero(Currency.USD)
    assert serializer.validated_data["currency"] == "USD"


@pytest.mark.parametrize(
    ("currency", "amount", "minor"),
    [("JPY", "1200", 1200), ("KWD", "12.500", 12500), ("RUB", "0.01", 1)],
)
def test_the_digits_follow_the_currency_next_to_it(
    currency: str, amount: str, minor: int
) -> None:
    serializer = ItemSerializer(data=valid(amount=amount, currency=currency))

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["amount"] == Money(minor, Currency(currency))


@pytest.mark.parametrize(
    "amount",
    [12.5, 12, None, True, ["12.50"], {"a": 1}, "twelve", "12,50", "1e2", "-12.50", ""],
)
def test_an_amount_that_is_not_a_decimal_string_is_invalid_amount(amount: Any) -> None:
    serializer = ItemSerializer(data=valid(amount=amount))

    assert not serializer.is_valid()
    assert codes(serializer)["amount"] in (["invalid_amount"], ["null"], ["blank"])
    assert "currency" not in serializer.errors


def test_a_json_number_is_never_accepted() -> None:
    serializer = ItemSerializer(data=valid(amount=12.5))

    assert not serializer.is_valid()
    assert codes(serializer) == {"amount": ["invalid_amount"]}


@pytest.mark.parametrize(
    ("amount", "currency"),
    [
        ("12.5", "USD"),  # too few digits
        ("12.500", "USD"),  # too many
        ("12.50", "JPY"),  # yen have none
        ("12", "KWD"),
        ("10000000000000.00", "USD"),  # beyond the largest amount
        ("0.00", "USD"),  # zero where it must be greater than zero
    ],
)
def test_an_amount_that_does_not_fit_the_currency_is_invalid_amount(
    amount: str, currency: str
) -> None:
    serializer = ItemSerializer(data=valid(amount=amount, currency=currency))

    assert not serializer.is_valid()
    assert codes(serializer) == {"amount": ["invalid_amount"]}


def test_zero_is_allowed_where_the_field_allows_it() -> None:
    serializer = ItemSerializer(data=valid(allowance="0.00"))

    assert serializer.is_valid(), serializer.errors


def test_every_wrong_amount_is_reported_at_once() -> None:
    serializer = ItemSerializer(data=valid(amount="1", allowance="2.555"))

    assert not serializer.is_valid()
    assert codes(serializer) == {
        "amount": ["invalid_amount"],
        "allowance": ["invalid_amount"],
    }


def test_the_error_does_not_repeat_the_amount() -> None:
    serializer = ItemSerializer(data=valid(amount="123.456"))

    serializer.is_valid()

    assert "123" not in str(serializer.errors["amount"][0])


def test_an_unknown_currency_is_refused_and_the_amount_is_not_judged() -> None:
    serializer = ItemSerializer(data=valid(currency="XXX"))

    assert not serializer.is_valid()
    assert codes(serializer) == {"currency": ["invalid_choice"]}


def test_a_lowercase_currency_is_refused() -> None:
    serializer = ItemSerializer(data=valid(currency="usd"))

    assert not serializer.is_valid()
    assert codes(serializer) == {"currency": ["invalid_choice"]}


def test_a_missing_currency_is_required() -> None:
    body = valid()
    del body["currency"]

    serializer = ItemSerializer(data=body)

    assert not serializer.is_valid()
    assert codes(serializer) == {"currency": ["required"]}


def test_a_partial_update_reads_the_currency_of_the_record() -> None:
    record = SimpleNamespace(currency="KWD")

    serializer = ItemSerializer(instance=record, data={"amount": "1.500"}, partial=True)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["amount"] == Money(1500, Currency.KWD)


def test_a_partial_update_judges_the_amount_by_the_record_currency() -> None:
    record = SimpleNamespace(currency="JPY")

    serializer = ItemSerializer(instance=record, data={"amount": "12.50"}, partial=True)

    assert not serializer.is_valid()
    assert codes(serializer) == {"amount": ["invalid_amount"]}


def test_a_partial_update_with_no_amount_needs_no_currency() -> None:
    serializer = ItemSerializer(data={}, partial=True)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {}


def test_a_partial_update_with_an_amount_and_no_currency_anywhere_is_refused() -> None:
    serializer = ItemSerializer(data={"amount": "1.00"}, partial=True)

    assert not serializer.is_valid()
    assert codes(serializer) == {"currency": ["required"]}


def test_an_amount_goes_out_with_exactly_the_currency_digits() -> None:
    data = BalanceSerializer(
        {"balance": Money(-12500, Currency.KWD), "currency": "KWD"}
    ).data

    assert data == {"balance": "-12.500", "currency": "KWD"}


@pytest.mark.parametrize("money", [Money(0, Currency.USD), Money(0, Currency.JPY)])
def test_zero_goes_out_without_a_sign(money: Money) -> None:
    data = BalanceSerializer({"balance": -money, "currency": money.currency.value}).data

    assert not data["balance"].startswith("-")


@pytest.mark.parametrize("value", ["12.50", 1250, 12.5, None])
def test_an_amount_field_writes_only_money(value: Any) -> None:
    with pytest.raises(TypeError):
        AmountField().to_representation(value)


def test_a_signed_amount_reads_a_minus_and_an_unsigned_one_does_not() -> None:
    assert SignedAmountField().to_internal_value("-5.00") == "-5.00"
    with pytest.raises(serializers.ValidationError):
        AmountField().to_internal_value("-5.00")


def test_the_two_types_carry_the_patterns_of_adr_0008() -> None:
    assert AmountField().pattern == AMOUNT_PATTERN
    assert SignedAmountField().pattern == SIGNED_AMOUNT_PATTERN
    assert AMOUNT_PATTERN == r"^(0|[1-9][0-9]*)(\.[0-9]{1,3})?$"
    assert SIGNED_AMOUNT_PATTERN == r"^-?(0|[1-9][0-9]*)(\.[0-9]{1,3})?$"


def test_a_currency_mismatch_is_a_400_on_the_currency_field() -> None:
    error = currency_mismatch()

    assert error.detail["currency"][0].code == "currency_mismatch"
    assert currency_mismatch("from").detail["from"][0].code == "currency_mismatch"


def test_the_currency_field_lists_exactly_the_supported_currencies() -> None:
    assert [c for c, _ in CurrencyField().choices.items()] == [
        c.value for c in Currency
    ]
