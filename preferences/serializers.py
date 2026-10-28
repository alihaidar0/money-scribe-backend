from rest_framework import serializers

from common.enums import PaymentMode
from common.serializers import (
    CurrencyField,
    RefuseUnknownFieldsMixin,
    StrictBooleanField,
    WholeNumberField,
)
from users.serializers import ClientSettingsMixin


class SettingsUpdateSerializer(RefuseUnknownFieldsMixin, ClientSettingsMixin):
    """The settings the person may change. Only the fields sent change; any other field is ``400 read_only``."""

    default_currency = CurrencyField(
        required=False,
        allow_null=True,
        help_text='`null` goes back to "not set": the app\'s default (`RUB`) applies.',
    )
    payment_mode = serializers.ChoiceField(
        choices=PaymentMode.choices,
        required=False,
        allow_null=True,
        help_text='How the person is paid. `null` goes back to "not set" (`one_payment`).',
    )
    first_pay_day = WholeNumberField(
        min_value=1,
        max_value=28,
        required=False,
        allow_null=True,
        help_text=(
            '1 to 28. `null` goes back to "not set" (20). The first pay day starts the first part of a month paid '
            "twice; the second pay day must be before it, or the change is `400` `invalid_pay_days`."
        ),
    )
    second_pay_day = WholeNumberField(
        min_value=1,
        max_value=28,
        required=False,
        allow_null=True,
        help_text=(
            '1 to 28. `null` goes back to "not set" (5). It starts the second part, in the month after the first pay '
            "day, and must be before the first pay day (`400` `invalid_pay_days`; a day never set counts as 20 or 5)."
        ),
    )
    remind_on_pay_days = StrictBooleanField(required=False)


class SettingsSerializer(serializers.Serializer):  # type: ignore[misc]
    """Every setting, with the app's own default for one that was never set."""

    language = serializers.CharField(read_only=True, help_text="`en` or `ar`.")
    time_zone = serializers.CharField(
        read_only=True, help_text="An IANA name such as `Asia/Riyadh`."
    )
    default_currency = CurrencyField(read_only=True)
    payment_mode = serializers.ChoiceField(choices=PaymentMode.choices, read_only=True)
    first_pay_day = serializers.IntegerField(read_only=True)
    second_pay_day = serializers.IntegerField(read_only=True)
    remind_on_pay_days = serializers.BooleanField(read_only=True)
    version = serializers.IntegerField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
