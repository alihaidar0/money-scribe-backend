"""The salary calculation (ADR 0007), without a database and without HTTP."""

import json
import random
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest

from common.money import MAX_MINOR_UNITS, AmountOutOfRange, Currency, Money
from salary.calculation import SalaryInputs, SalaryResults, calculate
from salary.models import PaymentMode

VECTORS = json.loads((Path(__file__).parent / "vectors.json").read_text())["cases"]


def inputs_of(case: dict[str, Any]) -> SalaryInputs:
    currency = Currency(case["currency"])
    raw = case["inputs"]
    return SalaryInputs(
        base_salary=Money.parse(raw["base_salary"], currency),
        food_per_day=Money.parse(raw["food_per_day"], currency),
        working_days_in_month=raw["working_days_in_month"],
        working_hours_in_month=raw["working_hours_in_month"],
        extra_hours_hundredths=raw["extra_hours_hundredths"],
        tax_rate_basis_points=raw["tax_rate_basis_points"],
        payment_mode=PaymentMode(raw["payment_mode"]),
        working_days_first_half=raw["working_days_first_half"],
    )


def written(results: SalaryResults) -> dict[str, str | None]:
    return {
        "food_allowance": results.food_allowance.to_string(),
        "extra_hours_pay": results.extra_hours_pay.to_string(),
        "income_before_tax": results.income_before_tax.to_string(),
        "tax": results.tax.to_string(),
        "total_after_tax": results.total_after_tax.to_string(),
        "first_payment": results.first_payment.to_string(),
        "second_payment": (
            None
            if results.second_payment is None
            else results.second_payment.to_string()
        ),
    }


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_the_shared_cases_give_the_figures_of_the_app_to_the_minor_unit(
    case: dict[str, Any],
) -> None:
    assert written(calculate(inputs_of(case))) == case["expected"]


def make(**changes: Any) -> SalaryInputs:
    base = {
        "base_salary": Money(10000000, Currency.RUB),
        "food_per_day": Money(50000, Currency.RUB),
        "working_days_in_month": 20,
        "working_hours_in_month": 160,
        "extra_hours_hundredths": 800,
        "tax_rate_basis_points": 1300,
        "payment_mode": PaymentMode.TWO_PAYMENTS,
        "working_days_first_half": 10,
    }
    return SalaryInputs(**{**base, **changes})


def test_a_one_payment_month_has_one_payment_and_no_second() -> None:
    results = calculate(
        make(payment_mode=PaymentMode.ONE_PAYMENT, working_days_first_half=None)
    )

    assert results.first_payment == results.total_after_tax
    assert results.second_payment is None


def test_two_payments_always_add_up_to_the_total_and_neither_is_negative() -> None:
    generator = random.Random(20261024)  # noqa: S311 - reproducible test data, not a secret
    for _ in range(3000):
        days = generator.randint(1, 31)
        inputs = make(
            base_salary=Money(generator.randint(1, 10**11), Currency.RUB),
            food_per_day=Money(generator.randint(0, 10**7), Currency.RUB),
            working_days_in_month=days,
            working_hours_in_month=generator.randint(1, 744),
            extra_hours_hundredths=generator.randint(0, 74400),
            tax_rate_basis_points=generator.randint(0, 10000),
            working_days_first_half=generator.randint(0, days),
        )

        results = calculate(inputs)

        assert results.second_payment is not None
        assert results.first_payment + results.second_payment == results.total_after_tax
        assert not results.first_payment.is_negative
        assert not results.second_payment.is_negative
        assert results.total_after_tax + results.tax == results.income_before_tax


def test_the_formulas_agree_with_decimal_round_half_up() -> None:
    """An independent implementation of the same formulas, in Decimal, over many random months."""
    generator = random.Random(1024)  # noqa: S311 - reproducible test data, not a secret

    def rounded(value: Decimal) -> int:
        return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))

    for _ in range(3000):
        base = generator.randint(1, 10**10)
        food = generator.randint(0, 10**6)
        days = generator.randint(1, 31)
        hours = generator.randint(1, 744)
        extra = generator.randint(0, 74400)
        rate = generator.randint(0, 10000)
        half = generator.randint(0, days)
        inputs = make(
            base_salary=Money(base, Currency.USD),
            food_per_day=Money(food, Currency.USD),
            working_days_in_month=days,
            working_hours_in_month=hours,
            extra_hours_hundredths=extra,
            tax_rate_basis_points=rate,
            working_days_first_half=half,
        )

        extra_pay = rounded(Decimal(base) * 2 * extra / (hours * 100))
        before = base + food * days + extra_pay
        tax = rounded(Decimal(before) * rate / 10000)
        total = before - tax
        first = min(
            rounded(Decimal(base) * half * (10000 - rate) / (days * 10000)), total
        )
        results = calculate(inputs)

        assert results.extra_hours_pay.minor == extra_pay
        assert results.income_before_tax.minor == before
        assert results.tax.minor == tax
        assert results.total_after_tax.minor == total
        assert results.first_payment.minor == first


def test_extra_hours_round_half_up() -> None:
    # 100 * 2 * 1 / (1 * 100) = 2.0 exactly; 25 * 2 * 1 / 100 = 0.5 -> 1; 24 * 2 * 1 / 100 = 0.48 -> 0
    one = {"working_hours_in_month": 1, "extra_hours_hundredths": 1}
    assert (
        calculate(
            make(base_salary=Money(100, Currency.RUB), **one)
        ).extra_hours_pay.minor
        == 2
    )
    assert (
        calculate(
            make(base_salary=Money(25, Currency.RUB), **one)
        ).extra_hours_pay.minor
        == 1
    )
    assert (
        calculate(
            make(base_salary=Money(24, Currency.RUB), **one)
        ).extra_hours_pay.minor
        == 0
    )


def test_the_tax_rounds_half_up_at_the_half_minor_unit() -> None:
    # 5 minor units at 10% is 0.5, which rounds up to 1
    results = calculate(
        make(
            base_salary=Money(5, Currency.RUB),
            food_per_day=Money(0, Currency.RUB),
            extra_hours_hundredths=0,
            tax_rate_basis_points=1000,
            payment_mode=PaymentMode.ONE_PAYMENT,
            working_days_first_half=None,
        )
    )

    assert results.tax.minor == 1
    assert results.total_after_tax.minor == 4


def test_no_tax_and_all_tax_are_the_two_ends() -> None:
    none = calculate(make(tax_rate_basis_points=0, extra_hours_hundredths=0))
    everything = calculate(make(tax_rate_basis_points=10000, extra_hours_hundredths=0))

    assert none.tax.is_zero
    assert none.total_after_tax == none.income_before_tax
    assert everything.total_after_tax.is_zero
    assert everything.first_payment.is_zero
    assert everything.second_payment is not None
    assert everything.second_payment.is_zero


def test_no_working_day_in_the_first_half_gives_the_second_payment_everything() -> None:
    results = calculate(make(working_days_first_half=0))

    assert results.first_payment.is_zero
    assert results.second_payment == results.total_after_tax


def test_the_currency_digits_do_not_change_the_arithmetic() -> None:
    """Minor units are minor units: yen and dinars use the same integer formulas."""
    for currency in (Currency.JPY, Currency.KWD):
        results = calculate(
            make(
                base_salary=Money(250000, currency),
                food_per_day=Money(1000, currency),
            )
        )

        assert results.food_allowance == Money(20000, currency)
        assert results.total_after_tax.currency is currency


def test_two_payments_without_the_first_half_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="first half"):
        calculate(make(working_days_first_half=None))


def test_a_figure_beyond_the_largest_amount_is_an_error_not_a_wrap() -> None:
    with pytest.raises(AmountOutOfRange):
        calculate(
            make(base_salary=Money(MAX_MINOR_UNITS, Currency.RUB))
        )  # the allowances take it over
    with pytest.raises(AmountOutOfRange):
        calculate(
            make(
                base_salary=Money(1, Currency.RUB),
                food_per_day=Money(MAX_MINOR_UNITS, Currency.RUB),
                working_days_in_month=2,
            )
        )
