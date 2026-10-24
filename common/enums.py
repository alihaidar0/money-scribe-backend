"""Choices that more than one app uses."""

from django.db import models


class PaymentMode(models.TextChoices):
    """How a person is paid: once a month, or in two parts (the salary month and the Settings both use it)."""

    ONE_PAYMENT = "one_payment", "One payment"
    TWO_PAYMENTS = "two_payments", "Two payments"
