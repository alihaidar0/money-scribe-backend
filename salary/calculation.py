"""The salary calculation (ADR 0007): one pure function on integer minor units, with no database and no HTTP.

    food_allowance    = food_per_day * working_days_in_month
    extra_hours_pay   = round(base * 2 * extra_hours_hundredths / (working_hours_in_month * 100))
    income_before_tax = base + food_allowance + extra_hours_pay
    tax               = round(income_before_tax * tax_rate_basis_points / 10000)
    total_after_tax   = income_before_tax - tax
    one payment:      payments = [total_after_tax]
    two payments:     first  = min(round(base * working_days_first_half * (10000 - rate) / (working_days * 10000)),
                                   total_after_tax)
                      second = total_after_tax - first

Rounding is half away from zero, once per formula, last, on integers (``common.money.divide_half_up``). The client
computes the same figures for its live preview; ``salary/tests/vectors.json`` is the file of invented cases both
sides run. A change of a formula is a new ``CALCULATION_VERSION``.
"""

from dataclasses import dataclass

from common.enums import PaymentMode
from common.money import Money, divide_half_up

BASIS_POINTS = 10000


@dataclass(frozen=True)
class SalaryInputs:
    base_salary: Money
    food_per_day: Money
    working_days_in_month: int
    working_hours_in_month: int
    extra_hours_hundredths: int
    tax_rate_basis_points: int
    payment_mode: PaymentMode
    working_days_first_half: int | None = None


@dataclass(frozen=True)
class SalaryResults:
    food_allowance: Money
    extra_hours_pay: Money
    income_before_tax: Money
    tax: Money
    total_after_tax: Money
    first_payment: Money
    second_payment: Money | None


def calculate(inputs: SalaryInputs) -> SalaryResults:
    """The results of one month. Raises ``AmountOutOfRange`` when a figure passes the largest amount."""
    base = inputs.base_salary
    currency = base.currency
    food_allowance = inputs.food_per_day * inputs.working_days_in_month
    extra_hours_pay = Money(
        divide_half_up(
            base.minor * 2 * inputs.extra_hours_hundredths,
            inputs.working_hours_in_month * 100,
        ),
        currency,
    )
    income_before_tax = base + food_allowance + extra_hours_pay
    tax = income_before_tax.scaled(inputs.tax_rate_basis_points, BASIS_POINTS)
    total_after_tax = income_before_tax - tax

    if inputs.payment_mode is PaymentMode.ONE_PAYMENT:
        first_payment, second_payment = total_after_tax, None
    else:
        if inputs.working_days_first_half is None:
            raise ValueError("Two payments need the working days of the first half.")
        share = Money(
            divide_half_up(
                base.minor
                * inputs.working_days_first_half
                * (BASIS_POINTS - inputs.tax_rate_basis_points),
                inputs.working_days_in_month * BASIS_POINTS,
            ),
            currency,
        )
        first_payment = min(share, total_after_tax)
        second_payment = total_after_tax - first_payment

    return SalaryResults(
        food_allowance=food_allowance,
        extra_hours_pay=extra_hours_pay,
        income_before_tax=income_before_tax,
        tax=tax,
        total_after_tax=total_after_tax,
        first_payment=first_payment,
        second_payment=second_payment,
    )
