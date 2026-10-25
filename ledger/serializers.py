from typing import Any

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.days import day_in_range
from common.serializers import (
    AmountField,
    CurrencyField,
    MoneySerializerMixin,
    RefuseUnknownFieldsMixin,
    TextField,
)
from config.error_codes import ErrorCode

from . import services
from .defaults import CUSTOM_ICONS
from .models import AccountKind, CategoryKind, TransactionKind


def _name_taken() -> ValidationError:
    """On the field itself (the field's own validator, so ``attr`` is ``name``)."""
    return ValidationError(
        "This name is already used.", code=ErrorCode.NAME_TAKEN.value
    )


# --- what the server answers ------------------------------------------------------------------


class AccountSerializer(serializers.Serializer):  # type: ignore[misc]
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    kind = serializers.ChoiceField(choices=AccountKind.choices, read_only=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class CategorySerializer(serializers.Serializer):  # type: ignore[misc]
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(
        read_only=True,
        help_text="A custom category's own name; for a built-in one the English fallback of its `key`.",
    )
    icon = serializers.CharField(
        read_only=True,
        help_text="A plain slug. Fall back to `other` for one the app does not know.",
    )
    kind = serializers.ChoiceField(choices=CategoryKind.choices, read_only=True)
    is_custom = serializers.BooleanField(read_only=True)
    key = serializers.CharField(
        read_only=True,
        allow_null=True,
        help_text="The stable key of a built-in category (show its name from the app's own strings); `null` for a custom one.",
    )
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


# --- what the client sends --------------------------------------------------------------------


class AccountInputSerializer(RefuseUnknownFieldsMixin, serializers.Serializer):  # type: ignore[misc]
    """An account to add or to change. Anything else (`id`, `version`, ...) is `400 read_only`."""

    name = TextField(
        max_length=30,
        help_text="1 to 30 characters, trimmed. Unique per person, in any case: `400 name_taken`.",
    )
    kind = serializers.ChoiceField(choices=AccountKind.choices)

    def validate_name(self, value: str) -> str:
        owner = self.context["request"].user
        if services.account_name_is_taken(owner, value, exclude=self.instance):
            raise _name_taken()
        return value

    def to_representation(self, instance: Any) -> dict[str, Any]:
        """The answer to a create or an edit is the account, as the output serializer shows it."""
        data: dict[str, Any] = AccountSerializer(instance, context=self.context).data
        return data


class CategoryInputSerializer(RefuseUnknownFieldsMixin, serializers.Serializer):  # type: ignore[misc]
    """A custom category to add or to change. Its kind is always `both`; a `kind` here is `400 read_only`."""

    name = TextField(
        max_length=30,
        help_text=(
            "1 to 30 characters, trimmed. Unique among the person's custom categories, in any case, and not a "
            "built-in key or English name: `400 name_taken`."
        ),
    )
    icon = serializers.ChoiceField(
        choices=[(icon, icon) for icon in CUSTOM_ICONS],
        help_text="One of the icons the picker offers for a custom category.",
    )

    def validate_name(self, value: str) -> str:
        owner = self.context["request"].user
        if services.category_name_is_taken(owner, value, exclude=self.instance):
            raise _name_taken()
        return value

    def to_representation(self, instance: Any) -> dict[str, Any]:
        data: dict[str, Any] = CategorySerializer(instance, context=self.context).data
        return data


# --- transactions -----------------------------------------------------------------------------


class TransactionSerializer(serializers.Serializer):  # type: ignore[misc]
    id = serializers.UUIDField(read_only=True)
    kind = serializers.ChoiceField(choices=TransactionKind.choices, read_only=True)
    amount = AmountField(read_only=True, source="money")
    currency = CurrencyField(read_only=True)
    date = serializers.DateField(read_only=True)
    category_id = serializers.UUIDField(read_only=True)
    account_id = serializers.UUIDField(read_only=True)
    note = serializers.CharField(read_only=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class TransactionCreateSerializer(
    RefuseUnknownFieldsMixin,
    MoneySerializerMixin,
    serializers.Serializer,  # type: ignore[misc]
):
    """A transaction to add. Anything else (`id`, `version`, ...) is `400 read_only`."""

    kind = serializers.ChoiceField(choices=TransactionKind.choices)
    amount = AmountField(allow_zero=False)
    currency = CurrencyField(
        help_text="Of the record, and fixed for good: it cannot be changed afterwards."
    )
    date = serializers.DateField(
        validators=[day_in_range],
        help_text="A calendar day, 2000-01-01 to 2099-12-31.",
    )
    category_id = serializers.UUIDField(
        help_text="A built-in category or one of the person's own, and one that allows the `kind`."
    )
    account_id = serializers.UUIDField(help_text="One of the person's accounts.")
    note = TextField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
        help_text="0 to 100 characters, trimmed.",
    )

    def to_representation(self, instance: Any) -> dict[str, Any]:
        data: dict[str, Any] = TransactionSerializer(
            instance, context=self.context
        ).data
        return data


class TransactionUpdateSerializer(
    RefuseUnknownFieldsMixin,
    MoneySerializerMixin,
    serializers.Serializer,  # type: ignore[misc]
):
    """A change to a transaction: the fields of the create except `currency`, which is `400 read_only`."""

    kind = serializers.ChoiceField(choices=TransactionKind.choices)
    amount = AmountField(allow_zero=False)
    date = serializers.DateField(
        validators=[day_in_range],
        help_text="A calendar day, 2000-01-01 to 2099-12-31.",
    )
    category_id = serializers.UUIDField()
    account_id = serializers.UUIDField()
    note = TextField(
        max_length=100, allow_blank=True, help_text="0 to 100 characters, trimmed."
    )

    def to_representation(self, instance: Any) -> dict[str, Any]:
        data: dict[str, Any] = TransactionSerializer(
            instance, context=self.context
        ).data
        return data
