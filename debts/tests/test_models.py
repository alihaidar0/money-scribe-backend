"""What the database itself refuses, whatever the API does (the rules of ``debts.models``)."""

import datetime
from typing import Any

import pytest
from django.db import IntegrityError, transaction

from debts.models import Debt, Repayment
from debts.tests.factories import DebtFactory, RepaymentFactory

pytestmark = pytest.mark.django_db


def refused_debt(**fields: Any) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DebtFactory.create(**fields)


@pytest.mark.parametrize("amount", [0, -1, 1_000_000_000_000_000])
def test_a_debt_amount_is_positive_and_within_the_largest_amount(amount: int) -> None:
    refused_debt(amount=amount)


def test_the_largest_amount_is_allowed() -> None:
    assert DebtFactory.create(amount=999_999_999_999_999).pk


def test_a_debt_needs_a_known_currency_and_direction_and_a_person() -> None:
    refused_debt(currency="XXX")
    refused_debt(direction="gave")
    refused_debt(person="")


def test_the_days_of_a_debt_are_in_range_and_the_due_day_is_not_before_the_start() -> (
    None
):
    refused_debt(start_date=datetime.date(1999, 12, 31))
    refused_debt(start_date=datetime.date(2100, 1, 1))
    refused_debt(due_date=datetime.date(2026, 9, 30))
    refused_debt(due_date=datetime.date(2100, 1, 1))
    assert DebtFactory.create(due_date=datetime.date(2026, 10, 1)).pk  # the same day
    assert DebtFactory.create(due_date=None).pk


@pytest.mark.parametrize("amount", [0, -5, 1_000_000_000_000_000])
def test_a_repayment_amount_is_positive_and_within_the_largest_amount(
    amount: int,
) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        RepaymentFactory.create(amount=amount)


def test_a_repayment_day_is_in_range() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        RepaymentFactory.create(date=datetime.date(1999, 12, 31))


def test_deleting_a_debt_deletes_its_repayments_and_only_those() -> None:
    debt = DebtFactory.create()
    RepaymentFactory.create(debt=debt)
    RepaymentFactory.create(debt=debt)
    kept = RepaymentFactory.create()

    debt.delete()

    assert list(Repayment.objects.all()) == [kept]


def test_deleting_the_owner_deletes_the_debts_and_their_repayments() -> None:
    debt = DebtFactory.create()
    RepaymentFactory.create(debt=debt)
    DebtFactory.create()

    debt.owner.delete()

    assert Debt.objects.count() == 1
    assert Repayment.objects.count() == 0


def test_a_new_debt_starts_at_version_1_and_a_save_raises_it() -> None:
    debt = DebtFactory.create()
    assert debt.version == 1

    debt.note = "Changed"
    debt.save()

    assert debt.version == 2
