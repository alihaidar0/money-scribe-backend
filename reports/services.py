"""The read models, computed from the saved records (ADR 0005): nothing here writes."""

import datetime
from typing import Any

from django.db.models import BigIntegerField, Case, F, QuerySet, Sum, When

from common.money import Currency, Money
from ledger.models import Transaction


def day_totals(
    queryset: QuerySet[Transaction], days: list[datetime.date]
) -> list[dict[str, Any]]:
    """One item per given day, newest first, each with its currencies by code: income minus expenses.

    The sums are exact (integer minor units in the database); a total is never added across currencies. A day that
    nets to zero shows ``0`` (never ``-0``). A total past the largest amount raises ``AmountOutOfRange``.
    """
    rows = (
        queryset.filter(date__in=days)
        .order_by()
        .values("date", "currency")
        .annotate(
            total=Sum(
                Case(
                    When(kind="income", then=F("amount")),
                    When(kind="expense", then=-F("amount")),
                    output_field=BigIntegerField(),
                )
            )
        )
    )
    by_day: dict[datetime.date, list[dict[str, Any]]] = {day: [] for day in days}
    for row in rows:
        currency = Currency(row["currency"])
        by_day[row["date"]].append(
            {"currency": currency.value, "total": Money(int(row["total"]), currency)}
        )
    return [
        {"date": day, "totals": sorted(by_day[day], key=lambda item: item["currency"])}
        for day in days
    ]
