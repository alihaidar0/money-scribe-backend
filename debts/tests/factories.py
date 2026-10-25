import datetime

from factory.declarations import SubFactory
from factory.django import DjangoModelFactory

from debts.models import Debt, Repayment
from users.tests.factories import VerifiedUserFactory


class DebtFactory(DjangoModelFactory[Debt]):
    """A debt of 200.00 USD lent to Alex, made straight in the database."""

    class Meta:
        model = Debt

    owner = SubFactory(VerifiedUserFactory)  # type: ignore[no-untyped-call]
    direction = "lent"
    person = "Alex"
    amount = 20000
    currency = "USD"
    start_date = datetime.date(2026, 10, 1)
    due_date = datetime.date(2026, 11, 1)
    note = "Laptop repair"


class RepaymentFactory(DjangoModelFactory[Repayment]):
    class Meta:
        model = Repayment

    debt = SubFactory(DebtFactory)  # type: ignore[no-untyped-call]
    amount = 5000
    date = datetime.date(2026, 10, 20)
