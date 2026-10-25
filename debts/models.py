"""Debts and their repayments (ADR 0005; ``docs/api-fields.md``).

Only what a person entered is stored. What was repaid, what remains and the status are derived when a debt is read
(``debts.presentation``), so they can never disagree with the repayments.
"""

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from common.days import FIRST_DAY, LAST_DAY
from common.models import VersionedModel
from common.money import MAX_MINOR_UNITS, Currency, Money

# At most this many debts per user, and this many repayments per debt.
MAX_DEBTS = 500
MAX_REPAYMENTS = 200


class Direction(models.TextChoices):
    LENT = "lent", "Lent"
    BORROWED = "borrowed", "Borrowed"


class Status(models.TextChoices):
    """The derived state of a debt; it is never stored."""

    OPEN = "open", "Open"
    OVERDUE = "overdue", "Overdue"
    SETTLED = "settled", "Settled"


class Debt(VersionedModel):
    """Money lent to someone or borrowed from someone, in one currency that never changes.

    ``person`` is the name of a third party: it is never written to a log. The ``amount`` is positive minor units and
    the ``direction`` says whose money it is.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="debts"
    )
    direction = models.CharField(max_length=8, choices=Direction)
    person = models.CharField(max_length=50)
    amount = models.BigIntegerField()
    currency = models.CharField(
        max_length=3, choices=[(c.value, c.value) for c in Currency]
    )
    start_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0, amount__lte=MAX_MINOR_UNITS),
                name="debts_debt_amount_range",
            ),
            models.CheckConstraint(
                condition=Q(currency__in=[c.value for c in Currency]),
                name="debts_debt_currency_supported",
            ),
            models.CheckConstraint(
                condition=Q(direction__in=[d.value for d in Direction]),
                name="debts_debt_direction_known",
            ),
            models.CheckConstraint(
                condition=~Q(person=""), name="debts_debt_person_not_empty"
            ),
            models.CheckConstraint(
                condition=Q(start_date__gte=FIRST_DAY, start_date__lte=LAST_DAY),
                name="debts_debt_start_date_range",
            ),
            models.CheckConstraint(
                condition=Q(due_date__isnull=True)
                | Q(due_date__gte=F("start_date"), due_date__lte=LAST_DAY),
                name="debts_debt_due_date_range",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # The list: newest first.
            models.Index(
                fields=["owner", "created_at", "id"], name="debts_debt_by_owner"
            )
        ]

    def __str__(self) -> str:
        return f"{self.direction} debt of {self.owner_id}"

    @property
    def money(self) -> Money:
        return Money(self.amount, Currency(self.currency))


class Repayment(VersionedModel):
    """A part of a debt paid back. It is in the debt's currency and carries none of its own."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    debt = models.ForeignKey(Debt, on_delete=models.CASCADE, related_name="repayments")
    amount = models.BigIntegerField()
    date = models.DateField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0, amount__lte=MAX_MINOR_UNITS),
                name="debts_repayment_amount_range",
            ),
            models.CheckConstraint(
                condition=Q(date__gte=FIRST_DAY, date__lte=LAST_DAY),
                name="debts_repayment_date_range",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["debt", "date", "id"], name="debts_repayment_by_debt")
        ]

    def __str__(self) -> str:
        return f"repayment of debt {self.debt_id} on {self.date}"
