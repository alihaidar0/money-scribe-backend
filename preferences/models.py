import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Q

from common.enums import PaymentMode
from common.models import VersionedModel
from common.money import Currency


class Preferences(VersionedModel):
    """What a person chose once and wants the server to remember (docs/api-fields.md, section Settings).

    One row per user, made the first time it is read. A field that was never set is an empty string (the two text
    columns) or ``NULL`` (the pay days): the API then answers the app's own default, and the salary form's defaults
    fall through to the most recent month. The language and the time zone are not here: they live on the user
    (ADR 0001), where sign-in already keeps them, and are changed through the same endpoint.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences"
    )
    default_currency = models.CharField(
        max_length=3,
        blank=True,
        default="",
        choices=[(c.value, c.value) for c in Currency],
    )
    payment_mode = models.CharField(
        max_length=12, blank=True, default="", choices=PaymentMode
    )
    first_pay_day = models.PositiveSmallIntegerField(null=True, blank=True)
    second_pay_day = models.PositiveSmallIntegerField(null=True, blank=True)
    remind_on_pay_days = models.BooleanField(default=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(default_currency="")
                | Q(default_currency__in=[c.value for c in Currency]),
                name="preferences_default_currency_supported",
            ),
            models.CheckConstraint(
                condition=Q(payment_mode="")
                | Q(payment_mode__in=[m.value for m in PaymentMode]),
                name="preferences_payment_mode_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(first_pay_day__isnull=True)
                    | Q(first_pay_day__gte=1, first_pay_day__lte=28)
                )
                & (
                    Q(second_pay_day__isnull=True)
                    | Q(second_pay_day__gte=1, second_pay_day__lte=28)
                ),
                name="preferences_pay_days_range",
            ),
        ]

    def __str__(self) -> str:
        return f"preferences of {self.user_id}"
