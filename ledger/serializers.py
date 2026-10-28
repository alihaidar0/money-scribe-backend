from typing import Any

from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, ValidationError

from common.days import day_in_range
from common.pay_cycle import PayDays, nearest_pay_day
from common.serializers import (
    AmountField,
    CurrencyField,
    MoneySerializerMixin,
    MonthField,
    RefuseUnknownFieldsMixin,
    TextField,
)
from config.error_codes import ErrorCode
from preferences.services import pay_days_of

from . import services
from .defaults import CUSTOM_ICONS
from .models import AccountKind, CategoryKind, TransactionKind, TransactionPart


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
    part = serializers.ChoiceField(
        choices=TransactionPart.choices,
        read_only=True,
        allow_null=True,
        help_text=(
            "Of an income: the payment it joins, the one the person chose or else the one whose pay day is nearest to "
            "the `date`. `null` for an expense. Only the Overview of a month paid twice uses it."
        ),
    )
    part_month = MonthField(
        read_only=True,
        allow_null=True,
        help_text="Of an income: the month the `part` belongs to (`YYYY-MM`). `null` for an expense.",
    )
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    _pay_days: PayDays | None = None

    def to_representation(self, instance: Any) -> dict[str, Any]:
        data: dict[str, Any] = super().to_representation(instance)
        data["part"], data["part_month"] = None, None
        if instance.kind != TransactionKind.INCOME.value:
            return data
        if instance.part:
            month, part = instance.part_month, instance.part
        else:
            if self._pay_days is None:  # read once for a whole page
                self._pay_days = pay_days_of(self.context["request"].user)
            month, part = nearest_pay_day(instance.date, self._pay_days)
        data["part"], data["part_month"] = part, MonthField().to_representation(month)
        return data


class _PartInputMixin(serializers.Serializer):  # type: ignore[misc]
    """``part`` and ``part_month`` of an income: both set or both ``null`` (ADR 0023)."""

    part = serializers.ChoiceField(
        choices=TransactionPart.choices,
        required=False,
        allow_null=True,
        help_text=(
            "Income only: the payment of a month paid twice the money joins. Send it with `part_month`, or send both "
            "as `null` for the part nearest to the `date`. Ignored by the Overview of a month paid once."
        ),
    )
    part_month = MonthField(
        required=False,
        allow_null=True,
        help_text="The month the `part` belongs to, `YYYY-MM` (2000-01 to 2099-12).",
    )

    def _invalid_part(self, message: str) -> ValidationError:
        return ValidationError(
            {"part": [ErrorDetail(message, code=ErrorCode.INVALID_PART.value)]}
        )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        sent = "part" in attrs or "part_month" in attrs
        if not sent:
            return attrs
        part, month = attrs.get("part"), attrs.get("part_month")
        if (part is None) != (month is None):
            raise self._invalid_part(
                "Send part and part_month together: both set or both null."
            )
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        if part is not None and kind != TransactionKind.INCOME.value:
            raise self._invalid_part("Only an income has a part.")
        return attrs


class TransactionCreateSerializer(
    RefuseUnknownFieldsMixin,
    _PartInputMixin,
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
    _PartInputMixin,
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
