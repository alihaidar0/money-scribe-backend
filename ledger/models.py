"""Accounts and categories (ADR 0005, 0017; ``docs/api-fields.md``)."""

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from common.models import VersionedModel

# At most this many accounts per user, and this many custom categories (the built-in ones do not count).
MAX_ACCOUNTS = 20
MAX_CUSTOM_CATEGORIES = 50
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
