import datetime
import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from common.enums import PaymentMode
from common.models import VersionedModel
from common.money import MAX_MINOR_UNITS, Currency, Money

CALCULATION_VERSION = 1
MAX_FIXED_ITEMS = 100
MAX_WORKING_HOURS = 744  # 31 days of 24 hours
MAX_EXTRA_HOURS_HUNDREDTHS = MAX_WORKING_HOURS * 100
FIRST_MONTH = datetime.date(2000, 1, 1)
LAST_MONTH = datetime.date(2099, 12, 1)


class ItemKind(models.TextChoices):
    INCOME = "income", "Income"
    EXPENSE = "expense", "Expense"


class Part(models.TextChoices):
    FIRST = "first", "First part"
    SECOND = "second", "Second part"


def _between(field: str, low: int, high: int) -> Q:
    return Q(**{f"{field}__gte": low, f"{field}__lte": high})


class SalaryMonth(VersionedModel):
    """One saved month of salary: the inputs, the results the server calculated from them, and nothing else.

    One per user and month (ADR 0007), addressed by the first day of the month. The inputs are in integer minor
    units of ``currency``; the results are stored with the ``calculation_version`` they were calculated under, so a
    later change of the formulas never rewrites history. The fixed items of the month are ``FixedItem`` rows.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="salary_months",
    )
    month = models.DateField()
    currency = models.CharField(
        max_length=3, choices=[(c.value, c.value) for c in Currency]
    )

    base_salary = models.BigIntegerField()
    food_per_day = models.BigIntegerField()
    working_days_in_month = models.PositiveSmallIntegerField()
    working_hours_in_month = models.PositiveSmallIntegerField()
    extra_hours_hundredths = models.PositiveIntegerField()
    tax_rate_basis_points = models.PositiveSmallIntegerField()
    payment_mode = models.CharField(max_length=12, choices=PaymentMode)
    working_days_first_half = models.PositiveSmallIntegerField(null=True, blank=True)
    first_pay_day = models.PositiveSmallIntegerField()
    second_pay_day = models.PositiveSmallIntegerField()

    calculation_version = models.PositiveSmallIntegerField(default=CALCULATION_VERSION)
    food_allowance = models.BigIntegerField()
    extra_hours_pay = models.BigIntegerField()
    income_before_tax = models.BigIntegerField()
    tax = models.BigIntegerField()
    total_after_tax = models.BigIntegerField()
    first_payment = models.BigIntegerField()
    second_payment = models.BigIntegerField(null=True, blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["owner", "month"], name="salary_month_owner_month_unique"
            ),
            models.CheckConstraint(
                condition=Q(month__day=1) & _between("month__year", 2000, 2099),
                name="salary_month_is_a_first_day_in_range",
            ),
            models.CheckConstraint(
                condition=Q(base_salary__gt=0, base_salary__lte=MAX_MINOR_UNITS),
                name="salary_month_base_salary_range",
            ),
            models.CheckConstraint(
                condition=_between("food_per_day", 0, MAX_MINOR_UNITS),
                name="salary_month_food_per_day_range",
            ),
            models.CheckConstraint(
                condition=_between("working_days_in_month", 1, 31),
                name="salary_month_working_days_range",
            ),
            models.CheckConstraint(
                condition=_between("working_hours_in_month", 1, MAX_WORKING_HOURS),
                name="salary_month_working_hours_range",
            ),
            models.CheckConstraint(
                condition=_between(
                    "extra_hours_hundredths", 0, MAX_EXTRA_HOURS_HUNDREDTHS
                ),
                name="salary_month_extra_hours_range",
            ),
            models.CheckConstraint(
                condition=_between("tax_rate_basis_points", 0, 10000),
                name="salary_month_tax_rate_range",
            ),
            models.CheckConstraint(
                condition=_between("first_pay_day", 1, 28)
                & _between("second_pay_day", 1, 28),
                name="salary_month_pay_days_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(payment_mode="one_payment", working_days_first_half__isnull=True)
                    | Q(
                        payment_mode="two_payments",
                        working_days_first_half__isnull=False,
                        working_days_first_half__gte=0,
                        working_days_first_half__lte=F("working_days_in_month"),
                    )
                ),
                name="salary_month_first_half_matches_the_mode",
            ),
            models.CheckConstraint(
                condition=(
                    _between("food_allowance", 0, MAX_MINOR_UNITS)
                    & _between("extra_hours_pay", 0, MAX_MINOR_UNITS)
                    & _between("income_before_tax", 0, MAX_MINOR_UNITS)
                    & _between("tax", 0, MAX_MINOR_UNITS)
                    & _between("total_after_tax", 0, MAX_MINOR_UNITS)
                    & _between("first_payment", 0, MAX_MINOR_UNITS)
                    & (
                        Q(second_payment__isnull=True)
                        | _between("second_payment", 0, MAX_MINOR_UNITS)
                    )
                ),
                name="salary_month_results_range",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["owner", "-month", "-id"], name="salary_month_by_owner"
            )
        ]

    def __str__(self) -> str:
        return f"salary month {self.month:%Y-%m} of {self.owner_id}"

    @property
    def money_currency(self) -> Currency:
        return Currency(self.currency)

    def money(self, minor: int) -> Money:
        return Money(minor, self.money_currency)


class FixedItem(VersionedModel):
    """A fixed income or expense of one salary month (ADR 0007). It has no currency of its own: the month's."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    salary_month = models.ForeignKey(
        SalaryMonth, on_delete=models.CASCADE, related_name="fixed_items"
    )
    name = models.CharField(max_length=50)
    amount = models.BigIntegerField()
    kind = models.CharField(max_length=7, choices=ItemKind)
    # Kept as entered and ignored while the month has one payment, so switching the mode loses nothing.
    part = models.CharField(max_length=6, choices=Part, default=Part.FIRST)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0, amount__lte=MAX_MINOR_UNITS),
                name="salary_fixeditem_amount_range",
            ),
            models.CheckConstraint(
                condition=~Q(name=""), name="salary_fixeditem_name_not_empty"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["salary_month", "created_at", "id"],
                name="salary_fixeditem_by_month",
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} item of {self.salary_month_id}"

    @property
    def currency(self) -> str:
        return str(self.salary_month.currency)

    @property
    def money(self) -> Money:
        return Money(self.amount, Currency(self.salary_month.currency))
