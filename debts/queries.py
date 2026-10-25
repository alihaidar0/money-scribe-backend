"""Queries over debts that the list filter and the debts report share."""

from django.db.models import BigIntegerField, OuterRef, QuerySet, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from .models import Debt, Repayment


def with_repaid(queryset: QuerySet[Debt]) -> QuerySet[Debt]:
    """Annotate every debt with ``repaid_total``, the sum of its repayments in minor units (0 for none).

    A subquery, not a join, so a debt with many repayments stays one row.
    """
    repaid = (
        Repayment.objects.filter(debt=OuterRef("pk"))
        .values("debt")
        .annotate(total=Sum("amount"))
        .values("total")
    )
    return queryset.annotate(
        repaid_total=Coalesce(
            Subquery(repaid, output_field=BigIntegerField()),
            Value(0),
            output_field=BigIntegerField(),
        )
    )
