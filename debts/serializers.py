from typing import Any

from rest_framework import serializers

from common.days import day_in_range
from common.money import Currency
from common.serializers import (
    AmountField,
    CurrencyField,
    MoneySerializerMixin,
    RefuseUnknownFieldsMixin,
    TextField,
)

from . import presentation
from .models import Direction, Status

DAY_HELP = "A calendar day, 2000-01-01 to 2099-12-31."


# --- what the server answers ------------------------------------------------------------------


class RepaymentSerializer(serializers.Serializer):  # type: ignore[misc]
    id = serializers.UUIDField(read_only=True)
    amount = AmountField(read_only=True, help_text="In the currency of the debt.")
    date = serializers.DateField(read_only=True)
    version = serializers.IntegerField(
        read_only=True,
        help_text="The `If-Match` of a change or a delete of this repayment (not the `ETag` of an answer).",
    )
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class DebtSerializer(serializers.Serializer):  # type: ignore[misc]
    """A debt with its repayments and the figures the server derives from them.

    The serializer is given a ``Debt`` and the person's ``today`` (in the context) and writes the derived values.
    """

    id = serializers.UUIDField(read_only=True)
    direction = serializers.ChoiceField(choices=Direction.choices, read_only=True)
    person = serializers.CharField(read_only=True)
    amount = AmountField(read_only=True)
    currency = CurrencyField(read_only=True)
    start_date = serializers.DateField(read_only=True)
    due_date = serializers.DateField(read_only=True, allow_null=True)
    note = serializers.CharField(read_only=True)
    repaid = AmountField(read_only=True, help_text="The sum of the repayments.")
    remaining = AmountField(
        read_only=True, help_text="The amount minus `repaid`, never below 0."
    )
    status = serializers.ChoiceField(
        choices=Status.choices,
        read_only=True,
        help_text=(
            "`settled` when nothing remains; `overdue` when a `due_date` is before the person's day and the debt "
            "is not settled; otherwise `open`."
        ),
    )
    repayments = RepaymentSerializer(
        many=True, read_only=True, help_text="By `date`, then `id`."
    )
    version = serializers.IntegerField(
        read_only=True,
        help_text="The `ETag` of every answer whose body is the debt, and the `If-Match` of its change or delete.",
    )
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def to_representation(self, instance: Any) -> dict[str, Any]:
        data: dict[str, Any] = super().to_representation(
            presentation.present_debt(instance, self.context["today"])
        )
        return data


# --- what the client sends --------------------------------------------------------------------


class DebtCreateSerializer(
    RefuseUnknownFieldsMixin,
    MoneySerializerMixin,
    serializers.Serializer,  # type: ignore[misc]
):
    """A debt to add. Anything else (`id`, `repaid`, `status`, `repayments`, `version`, ...) is `400 read_only`."""

    direction = serializers.ChoiceField(
        choices=Direction.choices,
        help_text="`lent` is money the person gave, `borrowed` money the person received. Fixed for good.",
    )
    person = TextField(max_length=50, help_text="1 to 50 characters, trimmed.")
    amount = AmountField(allow_zero=False)
    currency = CurrencyField(help_text="Of the debt, and fixed for good.")
    start_date = serializers.DateField(validators=[day_in_range], help_text=DAY_HELP)
    due_date = serializers.DateField(
        required=False,
        allow_null=True,
        validators=[day_in_range],
        help_text=f"{DAY_HELP} Not before `start_date` (`400 due_before_start`). `null` or left out: no due date.",
    )
    note = TextField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
        help_text="0 to 100 characters, trimmed.",
    )

    def to_representation(self, instance: Any) -> dict[str, Any]:
        data: dict[str, Any] = DebtSerializer(instance, context=self.context).data
        return data


class DebtUpdateSerializer(
    RefuseUnknownFieldsMixin,
    MoneySerializerMixin,
    serializers.Serializer,  # type: ignore[misc]
):
    """A change to a debt: the fields of the create except `direction` and `currency`, which are `400 read_only`."""

    person = TextField(max_length=50, help_text="1 to 50 characters, trimmed.")
    amount = AmountField(
        allow_zero=False,
        help_text=(
            "In the debt's currency. Not below what was repaid (`400 amount_below_repaid`); above it, a settled "
            "debt is open again."
        ),
    )
    start_date = serializers.DateField(
        validators=[day_in_range],
        help_text=f"{DAY_HELP} Not after the date of a repayment (`400 start_after_repayment`).",
    )
    due_date = serializers.DateField(
        allow_null=True,
        validators=[day_in_range],
        help_text=f"{DAY_HELP} Not before `start_date`. `null` clears it.",
    )
    note = TextField(
        max_length=100,
        allow_blank=True,
        help_text='0 to 100 characters, trimmed. `""` clears it; `null` is `400`.',
    )

    def to_representation(self, instance: Any) -> dict[str, Any]:
        data: dict[str, Any] = DebtSerializer(instance, context=self.context).data
        return data


class RepaymentInputSerializer(
    RefuseUnknownFieldsMixin,
    MoneySerializerMixin,
    serializers.Serializer,  # type: ignore[misc]
):
    """A repayment to add or to change. It is in the debt's currency; a `currency` here is `400 read_only`.

    The debt is in the context (``debt``): its currency reads the amount.
    """

    amount = AmountField(
        allow_zero=False,
        help_text=(
            "In the currency of the debt, greater than 0 and not more than what remains "
            "(`400 exceeds_remaining`, which also carries `remaining`)."
        ),
    )
    date = serializers.DateField(
        validators=[day_in_range],
        help_text=f"{DAY_HELP} Not before the debt's `start_date` (`400 repayment_before_start`).",
    )

    def _currency_of(self, attrs: dict[str, Any]) -> Currency:
        return Currency(self.context["debt"].currency)

    def to_representation(self, instance: Any) -> dict[str, Any]:
        """The answer to a repayment call is the whole debt."""
        data: dict[str, Any] = DebtSerializer(
            self.context["debt"], context=self.context
        ).data
        return data
