"""Accounts and categories (ADR 0005, 0017; ``docs/api-fields.md``)."""

import datetime
import uuid
from typing import ClassVar

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower, Upper

from common.models import VersionedModel
from common.money import MAX_MINOR_UNITS, Currency, Money

# At most this many accounts per user, and this many custom categories (the built-in ones do not count).
MAX_ACCOUNTS = 20
MAX_CUSTOM_CATEGORIES = 50
# At most this many transactions per user.
MAX_TRANSACTIONS = 100_000
# A transaction's date is a calendar day from this one to the last day of 2099 (the same range as a salary month).
FIRST_DAY = datetime.date(2000, 1, 1)
LAST_DAY = datetime.date(2099, 12, 31)
# A category's internal sort position: the built-in ones are 1 to 7 in a fixed order, a custom one is always last.
CUSTOM_POSITION = 100


class AccountKind(models.TextChoices):
    CARD = "card", "Card"
    CASH = "cash", "Cash"
    OTHER = "other", "Other"


class CategoryKind(models.TextChoices):
    EXPENSE = "expense", "Expense"
    INCOME = "income", "Income"
    BOTH = "both", "Both"


class Account(VersionedModel):
    """Where a person's money is kept: a card, cash, something else. It has no currency; a transaction does."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounts"
    )
    name = models.CharField(max_length=30)
    kind = models.CharField(max_length=5, choices=AccountKind)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # One name per person, whatever the case ("Card" and "card" are the same account).
            models.UniqueConstraint(
                Lower("name"), "owner", name="ledger_account_owner_name_unique"
            ),
            models.CheckConstraint(
                condition=~Q(name=""), name="ledger_account_name_not_empty"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["owner", "created_at", "id"], name="ledger_account_by_owner"
            )
        ]

    def __str__(self) -> str:
        return f"{self.kind} account of {self.owner_id}"


class Category(VersionedModel):
    """What a transaction was for. A built-in category is a shared row (no owner) with a stable ``key``; a custom
    one belongs to a person, has no key, and is always of the kind ``both``.

    ``position`` is internal: it puts the built-in categories first, in a fixed order, and the custom ones after.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="categories",
        null=True,
        blank=True,
    )
    key = models.CharField(max_length=20, null=True, blank=True)  # noqa: DJ001  # null means "custom"
    # A custom category's own name; for a built-in one, the English fallback of a key the app does not know yet.
    name = models.CharField(max_length=30)
    icon = models.CharField(max_length=40)
    kind = models.CharField(max_length=7, choices=CategoryKind)
    position = models.PositiveSmallIntegerField(default=CUSTOM_POSITION)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=(
                    Q(owner__isnull=True, key__isnull=False)
                    | Q(owner__isnull=False, key__isnull=True)
                ),
                name="ledger_category_builtin_or_custom",
            ),
            models.UniqueConstraint(
                fields=["key"],
                condition=Q(key__isnull=False),
                name="ledger_category_key_unique",
            ),
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                condition=Q(owner__isnull=False),
                name="ledger_category_owner_name_unique",
            ),
            models.CheckConstraint(
                condition=Q(owner__isnull=True) | Q(kind="both"),
                name="ledger_category_custom_is_both",
            ),
            models.CheckConstraint(
                condition=~Q(name=""), name="ledger_category_name_not_empty"
            ),
            models.CheckConstraint(
                condition=Q(icon__regex=r"^[a-z0-9_]{1,40}$"),
                name="ledger_category_icon_is_a_slug",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["owner", "position", "created_at", "id"],
                name="ledger_category_by_owner",
            )
        ]

    def __str__(self) -> str:
        return f"{'custom' if self.owner_id else 'built-in'} category {self.name}"

    @property
    def is_custom(self) -> bool:
        return self.owner_id is not None


class TransactionKind(models.TextChoices):
    EXPENSE = "expense", "Expense"
    INCOME = "income", "Income"


class Transaction(VersionedModel):
    """One income or expense: an amount, a day, a category and an account.

    The amount is positive minor units and the ``kind`` carries the sign. The currency is the record's own (an account
    has none) and cannot change, because that would reinterpret the stored amount. The category and the account are
    protected from deletion while a transaction uses them: a custom category's transactions are moved to the
    built-in ``other`` first, and an account that has transactions is refused (``ledger.services``).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    kind = models.CharField(max_length=7, choices=TransactionKind)
    amount = models.BigIntegerField()
    currency = models.CharField(
        max_length=3, choices=[(c.value, c.value) for c in Currency]
    )
    date = models.DateField()
    category = models.ForeignKey(
        Category, on_delete=models.RESTRICT, related_name="transactions"
    )
    account = models.ForeignKey(
        Account, on_delete=models.RESTRICT, related_name="transactions"
    )
    note = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(amount__gt=0, amount__lte=MAX_MINOR_UNITS),
                name="ledger_transaction_amount_range",
            ),
            models.CheckConstraint(
                condition=Q(currency__in=[c.value for c in Currency]),
                name="ledger_transaction_currency_supported",
            ),
            models.CheckConstraint(
                condition=Q(date__gte=FIRST_DAY, date__lte=LAST_DAY),
                name="ledger_transaction_date_range",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=[k.value for k in TransactionKind]),
                name="ledger_transaction_kind_known",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # The list and the day totals (ADR 0003): newest day first, then the entry added last.
            models.Index(
                fields=["owner", "date", "created_at", "id"],
                name="ledger_tx_by_owner_date",
            ),
            # The search of the note is a case-insensitive substring, which Django writes as
            # `UPPER(note) LIKE UPPER('%term%')`: the index is on that expression, so it can be used.
            GinIndex(
                OpClass(Upper("note"), name="gin_trgm_ops"), name="ledger_tx_note_trgm"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} of {self.owner_id} on {self.date}"

    @property
    def money(self) -> Money:
        return Money(self.amount, Currency(self.currency))
