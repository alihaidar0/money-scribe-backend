from typing import Any

from rest_framework import serializers

from common.serializers import AmountField, CurrencyField, SignedAmountField
from debts.models import Direction, Status


class CurrencyTotalSerializer(serializers.Serializer):  # type: ignore[misc]
    currency = CurrencyField(read_only=True)
    total = SignedAmountField(
        read_only=True,
        help_text="Income minus expenses of that day in that currency, with its digits; `-` when it is below zero.",
    )


class DayTotalSerializer(serializers.Serializer):  # type: ignore[misc]
    date = serializers.DateField(read_only=True)
    totals = CurrencyTotalSerializer(many=True, read_only=True)


# --- the Overview and the month report --------------------------------------------------------


class MonthKeySerializer(serializers.Serializer):  # type: ignore[misc]
    """The fields every month report has: the month, the currency, and what the month holds in any currency."""

    month = serializers.SerializerMethodField(help_text="`YYYY-MM`.")
    currency = CurrencyField(read_only=True, help_text="The currency of every amount.")
    has_data = serializers.BooleanField(read_only=True)
    currencies = serializers.ListField(
        child=CurrencyField(),
        read_only=True,
        help_text="Every currency that has something in the month, ordered by code; the amounts are in `currency` only.",
    )

    def get_month(self, instance: Any) -> str:
        return f"{instance.month:%Y-%m}"


class OverviewBlockSerializer(serializers.Serializer):  # type: ignore[misc]
    """The figures of the whole month or of one part, all positive except the `balance`."""

    salary = AmountField(
        read_only=True,
        help_text="The saved month's total after tax, or the payment of this part; 0 if the month was not saved in this currency.",
    )
    recurring_income = AmountField(
        read_only=True,
        help_text="The saved month's fixed income of this part (all of it with one payment).",
    )
    fixed_costs = AmountField(
        read_only=True,
        help_text="The saved month's fixed expenses of this part (all of them with one payment).",
    )
    income_transactions = AmountField(
        read_only=True,
        help_text="Income transactions dated in the period. The salary and the recurring income are not included.",
    )
    purchases = AmountField(
        read_only=True,
        help_text="Expense transactions dated in the period. The fixed costs are not included.",
    )
    debt_repayments_received = AmountField(
        read_only=True,
        help_text="Repayments dated in the period on debts the person lent.",
    )
    debt_repayments_made = AmountField(
        read_only=True,
        help_text="Repayments dated in the period on debts the person borrowed.",
    )
    balance = SignedAmountField(
        read_only=True,
        help_text=(
            "`salary` + `recurring_income` + `income_transactions` - `fixed_costs` - `purchases` + "
            "`debt_repayments_received` - `debt_repayments_made`."
        ),
    )


class OverviewSerializer(MonthKeySerializer):
    total = OverviewBlockSerializer(read_only=True, help_text="The whole month.")
    first_part = OverviewBlockSerializer(
        read_only=True,
        allow_null=True,
        help_text="Days 1 to 15; `null` for a month paid once.",
    )
    second_part = OverviewBlockSerializer(
        read_only=True,
        allow_null=True,
        help_text="Day 16 to the end of the month; `null` for a month paid once.",
    )


class CategorySpendSerializer(serializers.Serializer):  # type: ignore[misc]
    category_id = serializers.UUIDField(read_only=True)
    category_key = serializers.CharField(
        read_only=True,
        allow_null=True,
        help_text="The stable key of a built-in category; `null` for a custom one.",
    )
    name = serializers.CharField(
        read_only=True,
        help_text="A custom category's own name; for a built-in one the English fallback of its `key`.",
    )
    icon = serializers.CharField(read_only=True)
    amount = AmountField(
        read_only=True, help_text="Expense transactions of the category in the period."
    )


class ReportSliceSerializer(serializers.Serializer):  # type: ignore[misc]
    income_transactions = AmountField(
        read_only=True,
        help_text="Income transactions dated in the period. The salary and the recurring income are not included.",
    )
    purchases = AmountField(
        read_only=True,
        help_text="Expense transactions dated in the period. The fixed costs are not included.",
    )
    categories = CategorySpendSerializer(
        many=True,
        read_only=True,
        help_text="The spending by category (expense transactions only), the biggest `amount` first, ties by `category_id`; the amounts add up to `purchases`.",
    )


class MonthReportSerializer(MonthKeySerializer, ReportSliceSerializer):
    first_part = ReportSliceSerializer(
        read_only=True,
        allow_null=True,
        help_text="Days 1 to 15; `null` for a month paid once.",
    )
    second_part = ReportSliceSerializer(
        read_only=True,
        allow_null=True,
        help_text="Day 16 to the end of the month; `null` for a month paid once.",
    )


# --- the debts report -------------------------------------------------------------------------


class DebtLineSerializer(serializers.Serializer):  # type: ignore[misc]
    id = serializers.UUIDField(read_only=True)
    person = serializers.CharField(read_only=True)
    direction = serializers.ChoiceField(choices=Direction.choices, read_only=True)
    remaining = AmountField(read_only=True)
    status = serializers.ChoiceField(choices=Status.choices, read_only=True)
    due_date = serializers.DateField(read_only=True, allow_null=True)


class CurrencyDebtsSerializer(serializers.Serializer):  # type: ignore[misc]
    currency = CurrencyField(read_only=True)
    owed_to_you = AmountField(
        read_only=True,
        help_text="The `remaining` of the unsettled debts the person lent.",
    )
    you_owe = AmountField(
        read_only=True,
        help_text="The `remaining` of the unsettled debts the person borrowed.",
    )
    overdue = DebtLineSerializer(
        many=True,
        read_only=True,
        help_text="By `due_date`, the oldest first; ties by `id`.",
    )
    open = DebtLineSerializer(
        many=True,
        read_only=True,
        help_text="By `due_date`, the debts with none last; ties by `id`.",
    )


class DebtsReportSerializer(serializers.Serializer):  # type: ignore[misc]
    has_data = serializers.BooleanField(
        read_only=True,
        help_text='True when the person has any debt, settled or not: it tells "no debts at all" from "all settled".',
    )
    by_currency = CurrencyDebtsSerializer(
        many=True,
        read_only=True,
        help_text="One block per currency that has unsettled debts, ordered by currency code.",
    )
