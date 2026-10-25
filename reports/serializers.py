from rest_framework import serializers

from common.serializers import CurrencyField, SignedAmountField


class CurrencyTotalSerializer(serializers.Serializer):  # type: ignore[misc]
    currency = CurrencyField(read_only=True)
    total = SignedAmountField(
        read_only=True,
        help_text="Income minus expenses of that day in that currency, with its digits; `-` when it is below zero.",
    )


class DayTotalSerializer(serializers.Serializer):  # type: ignore[misc]
    date = serializers.DateField(read_only=True)
    totals = CurrencyTotalSerializer(many=True, read_only=True)
