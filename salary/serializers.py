"""The shapes of the salary API (docs/api-fields.md, ADR 0007, ADR 0008)."""

from typing import Any

from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail, ValidationError

from common.enums import PaymentMode
from common.money import Currency
from common.serializers import (
    AmountField,
    CurrencyField,
    MoneySerializerMixin,
    SignedAmountField,
    TextField,
    WholeNumberField,
    currency_mismatch,
)

from .models import (
    MAX_EXTRA_HOURS_HUNDREDTHS,
    MAX_WORKING_HOURS,
    ItemKind,
    Part,
)
from .presentation import present_item

# --- what the client sends --------------------------------------------------------------------


class SalaryMonthInputSerializer(MoneySerializerMixin, serializers.Serializer):  # type: ignore[misc]
    """The inputs of a month; the server calculates the rest and the client never sends a result."""

    currency = CurrencyField()
    base_salary = AmountField(allow_zero=False)
    food_per_day = AmountField()
    working_days_in_month = WholeNumberField(min_value=1, max_value=31)
    working_hours_in_month = WholeNumberField(min_value=1, max_value=MAX_WORKING_HOURS)
    extra_hours_hundredths = WholeNumberField(
        min_value=0,
        max_value=MAX_EXTRA_HOURS_HUNDREDTHS,
        help_text="Hours in hundredths: 750 is 7.5 hours.",
    )
    tax_rate_basis_points = WholeNumberField(
        min_value=0, max_value=10000, help_text="1300 is 13%."
    )
    payment_mode = serializers.ChoiceField(choices=PaymentMode.choices)
    working_days_first_half = WholeNumberField(
        min_value=0,
        required=False,
        allow_null=True,
        help_text=(
            "The working days of the first half of the month, up to `working_days_in_month`. Required with two "
            "payments; with one payment it is ignored and stored as `null`."
        ),
    )
    first_pay_day = WholeNumberField(min_value=1, max_value=28)
    second_pay_day = WholeNumberField(min_value=1, max_value=28)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        if attrs["payment_mode"] == PaymentMode.ONE_PAYMENT:
            attrs["working_days_first_half"] = None
            return attrs
        half = attrs.get("working_days_first_half")
        if half is None:
            raise ValidationError(
                {
                    "working_days_first_half": [
                        ErrorDetail("Required with two payments.", code="required")
                    ]
                }
            )
        if half > attrs["working_days_in_month"]:
            raise ValidationError(
                {
                    "working_days_first_half": [
                        ErrorDetail(
                            "At most the working days of the month.", code="max_value"
                        )
                    ]
                }
            )
        return attrs


class FixedItemInputSerializer(MoneySerializerMixin, serializers.Serializer):  # type: ignore[misc]
    """A fixed item to add or to change. It has no currency of its own: the month's."""

    name = TextField(max_length=50, help_text="1 to 50 characters, trimmed.")
    amount = AmountField(allow_zero=False)
    kind = serializers.ChoiceField(choices=ItemKind.choices)
    part = serializers.ChoiceField(
        choices=Part.choices,
        default=Part.FIRST.value,
        help_text="Ignored while the month has one payment (every item counts then).",
    )
    currency = CurrencyField(
        required=False,
        help_text="May be left out. If it is sent it must be the currency of the month.",
    )

    def _month_currency(self) -> Currency:
        if self.instance is not None:
            return Currency(self.instance.currency)
        return Currency(str(self.context["currency"]))

    def _currency_of(self, attrs: dict[str, Any]) -> Currency:
        return self._month_currency()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        sent = attrs.get("currency")
        if sent is not None and sent != self._month_currency().value:
            raise currency_mismatch()
        return super().validate(attrs)

    def to_representation(self, instance: Any) -> dict[str, Any]:
        """The answer to a create or an edit is the item, as the output serializer shows it."""
        data: dict[str, Any] = FixedItemSerializer(
            present_item(instance), context=self.context
        ).data
        return data


# --- what the server answers ------------------------------------------------------------------


class FixedItemSerializer(serializers.Serializer):  # type: ignore[misc]
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    amount = AmountField(read_only=True)
    currency = CurrencyField(read_only=True, help_text="The currency of the month.")
    kind = serializers.ChoiceField(choices=ItemKind.choices, read_only=True)
    part = serializers.ChoiceField(choices=Part.choices, read_only=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class ResultsSerializer(serializers.Serializer):  # type: ignore[misc]
    food_allowance = AmountField(read_only=True)
    extra_hours_pay = AmountField(read_only=True)
    income_before_tax = AmountField(read_only=True)
    tax = AmountField(read_only=True)
    total_after_tax = AmountField(read_only=True)


class PaymentSerializer(serializers.Serializer):  # type: ignore[misc]
    part = serializers.ChoiceField(choices=Part.choices, read_only=True)
    amount = AmountField(read_only=True)


class PartTotalsSerializer(serializers.Serializer):  # type: ignore[misc]
    income = AmountField(read_only=True)
    expense = AmountField(read_only=True)


class FixedTotalsSerializer(serializers.Serializer):  # type: ignore[misc]
    first = PartTotalsSerializer(read_only=True)
    second = PartTotalsSerializer(read_only=True, allow_null=True)


class BalancesSerializer(serializers.Serializer):  # type: ignore[misc]
    first = SignedAmountField(read_only=True)
    second = SignedAmountField(read_only=True, allow_null=True)
    month = SignedAmountField(read_only=True)


class SalaryMonthSerializer(serializers.Serializer):  # type: ignore[misc]
    """A saved month with its results, its fixed items, their totals and the balances. Read only."""

    id = serializers.UUIDField(read_only=True)
    month = serializers.CharField(read_only=True, help_text="`YYYY-MM`.")
    currency = CurrencyField(read_only=True)
    base_salary = AmountField(read_only=True)
    food_per_day = AmountField(read_only=True)
    working_days_in_month = serializers.IntegerField(read_only=True)
    working_hours_in_month = serializers.IntegerField(read_only=True)
    extra_hours_hundredths = serializers.IntegerField(read_only=True)
    tax_rate_basis_points = serializers.IntegerField(read_only=True)
    payment_mode = serializers.ChoiceField(choices=PaymentMode.choices, read_only=True)
    working_days_first_half = serializers.IntegerField(read_only=True, allow_null=True)
    first_pay_day = serializers.IntegerField(read_only=True)
    second_pay_day = serializers.IntegerField(read_only=True)
    calculation_version = serializers.IntegerField(
        read_only=True,
        help_text="The version of the formulas the results were calculated under.",
    )
    results = ResultsSerializer(read_only=True)
    payments = PaymentSerializer(
        many=True, read_only=True, help_text="One entry, `first`, or two."
    )
    fixed_items = FixedItemSerializer(many=True, read_only=True)
    fixed_totals = FixedTotalsSerializer(read_only=True)
    balances = BalancesSerializer(read_only=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def to_representation(self, instance: Any) -> dict[str, Any]:
        from .services import checked

        data: dict[str, Any] = super().to_representation(
            instance if isinstance(instance, dict) else checked(instance)
        )
        return data


class DefaultsSerializer(serializers.Serializer):  # type: ignore[misc]
    """The inputs that pre-fill the form of a month that is not saved yet."""

    currency = CurrencyField(read_only=True)
    base_salary = AmountField(read_only=True, allow_null=True)
    food_per_day = AmountField(read_only=True, allow_null=True)
    working_days_in_month = serializers.IntegerField(read_only=True, allow_null=True)
    working_hours_in_month = serializers.IntegerField(read_only=True, allow_null=True)
    extra_hours_hundredths = serializers.IntegerField(read_only=True)
    tax_rate_basis_points = serializers.IntegerField(read_only=True)
    payment_mode = serializers.ChoiceField(choices=PaymentMode.choices, read_only=True)
    working_days_first_half = serializers.IntegerField(read_only=True, allow_null=True)
    first_pay_day = serializers.IntegerField(read_only=True)
    second_pay_day = serializers.IntegerField(read_only=True)


class CopyFixedItemsSerializer(serializers.Serializer):  # type: ignore[misc]
    """The body of the copy: ``{"from": "YYYY-MM"}``, which may be left out. ``from`` is a Python keyword."""

    def get_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = super().get_fields()
        fields["from"] = serializers.RegexField(
            r"^20[0-9]{2}-(0[1-9]|1[0-2])$",
            required=False,
            help_text=(
                "The month to copy the items from, `YYYY-MM`. Default: the most recent saved month before this one."
            ),
        )
        return fields
