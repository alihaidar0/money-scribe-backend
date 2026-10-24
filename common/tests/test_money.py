"""The Money type: integer minor units, half-up rounding, one currency at a time, the wire form of ADR 0008."""

import dataclasses
import random
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest

from common.money import (
    MAX_MINOR_UNITS,
    AmountOutOfRange,
    Currency,
    CurrencyMismatch,
    InvalidAmount,
    Money,
    MoneyError,
    UnknownCurrency,
    divide_half_up,
)

USD, RUB, JPY, KWD = Currency.USD, Currency.RUB, Currency.JPY, Currency.KWD


# --- the currencies ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "digits"),
    [
        ("RUB", 2),
        ("USD", 2),
        ("EUR", 2),
        ("GBP", 2),
        ("TRY", 2),
        ("SAR", 2),
        ("AED", 2),
        ("EGP", 2),
        ("JPY", 0),
        ("KWD", 3),
    ],
)
def test_each_currency_has_its_number_of_digits(code: str, digits: int) -> None:
    assert Currency(code).digits == digits


def test_exactly_the_supported_currencies_exist() -> None:
    assert {c.value for c in Currency} == {
        "RUB",
        "USD",
        "EUR",
        "GBP",
        "TRY",
        "SAR",
        "AED",
        "EGP",
        "JPY",
        "KWD",
    }


def test_a_currency_code_is_read_exactly() -> None:
    assert Currency.parse("USD") is USD


@pytest.mark.parametrize("code", ["usd", "Usd", "XXX", "", "USD ", None, 840, b"USD"])
def test_anything_else_is_an_unknown_currency(code: object) -> None:
    with pytest.raises(UnknownCurrency):
        Currency.parse(code)


# --- rounding ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (5, 2, 3),  # 2.5 -> 3
        (-5, 2, -3),  # -2.5 -> -3: away from zero, not toward it
        (4, 2, 2),
        (7, 3, 2),  # 2.33
        (8, 3, 3),  # 2.67
        (-7, 3, -2),
        (-8, 3, -3),
        (1, 3, 0),
        (2, 3, 1),
        (1, 2, 1),  # 0.5 -> 1
        (-1, 2, -1),
        (0, 7, 0),
        (-1, 3, 0),  # never "-0"
        (10, 1, 10),
    ],
)
def test_rounding_is_half_away_from_zero(
    numerator: int, denominator: int, expected: int
) -> None:
    assert divide_half_up(numerator, denominator) == expected


def test_rounding_agrees_with_decimal_round_half_up() -> None:
    """The integer routine is the same as Decimal's ROUND_HALF_UP on a wide spread of inputs."""
    generator = random.Random(20261024)  # noqa: S311 - reproducible test data, not a secret
    for _ in range(5000):
        numerator = generator.randint(-(10**12), 10**12)
        denominator = generator.randint(1, 10**6)
        expected = int(
            (Decimal(numerator) / Decimal(denominator)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )
        assert divide_half_up(numerator, denominator) == expected


@pytest.mark.parametrize("denominator", [0, -1, -100])
def test_the_denominator_must_be_positive(denominator: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        divide_half_up(1, denominator)


def test_scaled_rounds_once_at_the_end() -> None:
    # 15% of 336568 = 50485.2, which rounds to 50485 (the tax of the worked example in ADR 0007)
    assert Money(336568, RUB).scaled(1500, 10000) == Money(50485, RUB)
    # half a kopeck rounds up, and a negative amount rounds away from zero as well
    assert Money(5, RUB).scaled(1, 2) == Money(3, RUB)
    assert Money(-5, RUB).scaled(1, 2) == Money(-3, RUB)


def test_scaled_keeps_the_currency_and_never_mixes() -> None:
    assert Money(1000, JPY).scaled(1, 3).currency is JPY


# --- parsing the wire form --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "currency", "minor"),
    [
        ("12.50", USD, 1250),
        ("0.00", USD, 0),
        ("0.05", USD, 5),
        ("1234567.89", RUB, 123456789),
        ("1200", JPY, 1200),
        ("0", JPY, 0),
        ("12.500", KWD, 12500),
        ("0.001", KWD, 1),
        ("9999999999999.99", USD, MAX_MINOR_UNITS),  # the largest amount
        ("999999999999999", JPY, MAX_MINOR_UNITS),
        ("999999999999.999", KWD, MAX_MINOR_UNITS),
    ],
)
def test_a_valid_amount_is_read(text: str, currency: Currency, minor: int) -> None:
    assert Money.parse(text, currency) == Money(minor, currency)


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        " 12.50",
        "12.50 ",
        "12.50\n",  # a trailing newline is not part of an amount
        "12.5",  # too few digits: the currency has exactly two
        "12",
        "12.500",  # too many digits
        "12.",
        ".50",
        "012.50",  # a leading zero
        "00.50",
        "1,234.50",  # grouping
        "1 234.50",
        "12,50",  # the wrong separator
        "1e2",  # an exponent
        "1.50e1",
        "+12.50",  # a plus sign
        "0x10",
        "NaN",
        "Infinity",
        "12.50.1",
        "\u0661\u0662.\u0665\u0660",  # Arabic-Indic digits are not ASCII digits
        "\uff11\uff12.\uff15\uff10",  # full-width digits
        "12.\uff150",
        "-12.50",  # unsigned input
        "-0.00",
        "10000000000000.00",  # one more than the largest amount
        "9" * 40,  # far beyond anything: refused before it is converted
    ],
)
def test_anything_else_is_not_an_amount_of_dollars(text: str) -> None:
    with pytest.raises(InvalidAmount):
        Money.parse(text, USD)


@pytest.mark.parametrize("text", ["12.0", "12.00", "12.", "1.5"])
def test_yen_have_no_fraction_digits(text: str) -> None:
    with pytest.raises(InvalidAmount):
        Money.parse(text, JPY)


@pytest.mark.parametrize("text", ["1.00", "1.0", "1.0000", "1"])
def test_dinars_have_exactly_three_fraction_digits(text: str) -> None:
    with pytest.raises(InvalidAmount):
        Money.parse(text, KWD)


@pytest.mark.parametrize(
    "value", [12, 12.5, Decimal("12.50"), None, True, b"12.50", ["1"]]
)
def test_an_amount_is_a_string_never_a_number(value: Any) -> None:
    with pytest.raises(InvalidAmount):
        Money.parse(value, USD)


def test_a_signed_amount_may_be_negative_but_never_negative_zero() -> None:
    assert Money.parse("-12.50", USD, signed=True) == Money(-1250, USD)
    assert Money.parse("12.50", USD, signed=True) == Money(1250, USD)
    for zero in ("-0", "-0.00"):
        with pytest.raises(InvalidAmount):
            Money.parse(zero, USD if "." in zero else JPY, signed=True)


def test_zero_can_be_refused_for_an_amount_that_must_be_positive() -> None:
    with pytest.raises(InvalidAmount, match="greater than zero"):
        Money.parse("0.00", USD, allow_zero=False)
    assert Money.parse("0.01", USD, allow_zero=False) == Money(1, USD)


def test_the_error_message_never_repeats_the_amount() -> None:
    with pytest.raises(InvalidAmount) as error:
        Money.parse("123.456", USD)

    assert "123" not in str(error.value)


def test_an_amount_beyond_the_largest_is_out_of_range() -> None:
    with pytest.raises(AmountOutOfRange):
        Money.parse("10000000000000.00", USD)


# --- writing the wire form --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("money", "text"),
    [
        (Money(1250, USD), "12.50"),
        (Money(0, USD), "0.00"),
        (Money(5, USD), "0.05"),
        (Money(100, USD), "1.00"),
        (Money(1200, JPY), "1200"),
        (Money(0, JPY), "0"),
        (Money(12500, KWD), "12.500"),
        (Money(1, KWD), "0.001"),
        (Money(-1250, USD), "-12.50"),
        (Money(-1, KWD), "-0.001"),
        (Money(MAX_MINOR_UNITS, USD), "9999999999999.99"),
        (Money(-MAX_MINOR_UNITS, JPY), "-999999999999999"),
    ],
)
def test_an_amount_is_written_with_exactly_the_currency_digits(
    money: Money, text: str
) -> None:
    assert money.to_string() == text


@pytest.mark.parametrize("currency", list(Currency))
def test_what_is_written_is_read_back_unchanged(currency: Currency) -> None:
    generator = random.Random(sum(currency.value.encode()))  # noqa: S311 - reproducible test data
    for _ in range(500):
        minor = generator.randint(-MAX_MINOR_UNITS, MAX_MINOR_UNITS)
        money = Money(minor, currency)

        assert Money.parse(money.to_string(), currency, signed=True) == money


@pytest.mark.parametrize("currency", list(Currency))
def test_zero_is_never_written_with_a_sign(currency: Currency) -> None:
    assert not Money.zero(currency).to_string().startswith("-")
    assert not (-Money.zero(currency)).to_string().startswith("-")


def test_to_decimal_places_the_point_by_the_currency() -> None:
    assert Money(1250, USD).to_decimal() == Decimal("12.50")
    assert Money(12500, KWD).to_decimal() == Decimal("12.500")
    assert Money(1200, JPY).to_decimal() == Decimal(1200)
    assert Money(-5, USD).to_decimal() == Decimal("-0.05")


# --- the value --------------------------------------------------------------------------------


def test_money_is_immutable_and_hashable() -> None:
    money = Money(100, USD)

    with pytest.raises(dataclasses.FrozenInstanceError):
        money.minor = 5  # type: ignore[misc]
    assert {money, Money(100, USD)} == {money}


@pytest.mark.parametrize("minor", [1.5, "100", None, True, Decimal(1)])
def test_minor_units_are_a_whole_number(minor: Any) -> None:
    with pytest.raises(TypeError):
        Money(minor, USD)


def test_the_currency_is_a_currency_not_a_string() -> None:
    with pytest.raises(TypeError):
        Money(100, "USD")  # type: ignore[arg-type]


@pytest.mark.parametrize("minor", [MAX_MINOR_UNITS + 1, -MAX_MINOR_UNITS - 1])
def test_a_value_beyond_the_range_cannot_exist(minor: int) -> None:
    with pytest.raises(AmountOutOfRange):
        Money(minor, USD)


def test_the_range_is_fifteen_digits() -> None:
    assert MAX_MINOR_UNITS == 999_999_999_999_999
    assert len(str(MAX_MINOR_UNITS)) == 15
    assert MAX_MINOR_UNITS < 2**53  # below JavaScript's largest safe integer


def test_errors_share_a_base() -> None:
    assert issubclass(InvalidAmount, MoneyError)
    assert issubclass(AmountOutOfRange, InvalidAmount)
    assert issubclass(CurrencyMismatch, MoneyError)
    assert issubclass(MoneyError, ValueError)


# --- arithmetic -------------------------------------------------------------------------------


def test_amounts_of_one_currency_add_and_subtract_exactly() -> None:
    assert Money(1050, USD) + Money(250, USD) == Money(1300, USD)
    assert Money(1050, USD) - Money(2050, USD) == Money(-1000, USD)
    assert -Money(5, USD) == Money(-5, USD)
    assert abs(Money(-5, USD)) == Money(5, USD)


def test_a_whole_number_multiplies_exactly() -> None:
    assert Money(50000, RUB) * 22 == Money(1100000, RUB)
    assert 22 * Money(50000, RUB) == Money(1100000, RUB)
    assert Money(50000, RUB) * 0 == Money.zero(RUB)


@pytest.mark.parametrize("factor", [1.5, Decimal(2), "2", None, True])
def test_only_a_whole_number_multiplies(factor: Any) -> None:
    with pytest.raises(TypeError):
        Money(100, USD) * factor


def test_a_number_is_not_an_amount() -> None:
    with pytest.raises(TypeError):
        Money(100, USD) + 5
    with pytest.raises(TypeError):
        Money(100, USD) - 5
    with pytest.raises(TypeError):
        _ = Money(100, USD) < 5


@pytest.mark.parametrize(
    "operation",
    [
        lambda a, b: a + b,
        lambda a, b: a - b,
        lambda a, b: a < b,
        lambda a, b: a <= b,
        lambda a, b: a > b,
        lambda a, b: a >= b,
    ],
    ids=["add", "subtract", "lt", "le", "gt", "ge"],
)
def test_two_currencies_are_never_combined_or_ordered(operation: Any) -> None:
    with pytest.raises(CurrencyMismatch):
        operation(Money(100, USD), Money(100, RUB))


def test_two_currencies_are_simply_not_equal() -> None:
    assert Money(100, USD) != Money(100, RUB)
    assert Money(100, USD) != 100  # type: ignore[comparison-overlap]


def test_arithmetic_beyond_the_range_is_an_error() -> None:
    top = Money(MAX_MINOR_UNITS, USD)

    with pytest.raises(AmountOutOfRange):
        top + Money(1, USD)
    with pytest.raises(AmountOutOfRange):
        -top - Money(1, USD)
    with pytest.raises(AmountOutOfRange):
        top * 2
    with pytest.raises(AmountOutOfRange):
        top.scaled(2, 1)


def test_the_order_of_one_currency() -> None:
    values = [Money(5, USD), Money(-3, USD), Money(0, USD)]

    assert sorted(values) == [Money(-3, USD), Money(0, USD), Money(5, USD)]
    assert Money(5, USD) > Money(4, USD) >= Money(4, USD) > Money(-1, USD)
    assert Money(1, USD) <= Money(1, USD)


def test_the_sum_of_some_amounts() -> None:
    items = [Money(100, USD), Money(250, USD), Money(-50, USD)]

    assert Money.sum(USD, items) == Money(300, USD)
    assert Money.sum(USD, []) == Money.zero(USD)
    with pytest.raises(CurrencyMismatch):
        Money.sum(USD, [Money(1, RUB)])


def test_what_an_amount_is() -> None:
    assert Money(0, USD).is_zero
    assert Money(1, USD).is_positive
    assert Money(-1, USD).is_negative
    assert not Money(0, USD).is_positive
    assert not Money(0, USD).is_negative
