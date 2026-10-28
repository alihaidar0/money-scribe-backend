"""The transaction sums of a month cut into its parts, in every currency (ADR 0023).

A month paid once is cut by calendar date. A month paid twice cuts expenses by the date through the pay cycle, and puts
each income on the part the person chose (``part`` and ``part_month``) or, when none was chosen, on the part whose pay
day is nearest to its date. The nearest part is worked out from the current Settings, so changing the pay days re-cuts
the history: nothing about it is stored.
"""

import datetime
from collections import defaultdict
from typing import Any

from django.db.models import Case, IntegerField, Sum, Value, When

from common.pay_cycle import FIRST as FIRST_PART
from common.pay_cycle import PayDays, nearest_pay_day
from ledger.models import Transaction
from users.models import User

from .periods import FIRST, SECOND, MonthPeriod

CATEGORY_FIELDS = ("category_id", "category__key", "category__name", "category__icon")
# The nearest pay day of an income is within one month of it, so this window holds every income that can land here.
WINDOW = datetime.timedelta(days=31)


def transaction_rows(
    user: User,
    period: MonthPeriod,
    pay_days: PayDays | None,
    *,
    by_category: bool = False,
) -> list[dict[str, Any]]:
    """Rows of ``kind``, ``currency``, ``half`` and ``total`` (and the category when asked), for every currency.

    ``pay_days`` is ``None`` for a month paid once. An income row has no category; the report only sums it.
    """
    fields = ["kind", "currency", "half", *(CATEGORY_FIELDS if by_category else ())]
    owned = Transaction.objects.filter(owner=user).order_by()
    in_period = {"date__gte": period.first_day, "date__lte": period.last_day}
    if pay_days is None:
        return list(
            owned.filter(**in_period)
            .annotate(half=period.half())
            .values(*fields)
            .annotate(total=Sum("amount"))
        )
    rows = list(
        owned.filter(kind="expense", **in_period)
        .annotate(half=period.half())
        .values(*fields)
        .annotate(total=Sum("amount"))
    )
    empty = dict.fromkeys(CATEGORY_FIELDS if by_category else ())
    for (currency, half), total in _income_totals(period, pay_days, owned).items():
        rows.append(
            {
                "kind": "income",
                "currency": currency,
                "half": half,
                "total": total,
                **empty,
            }
        )
    return rows


def _income_totals(
    period: MonthPeriod, pay_days: PayDays, owned: Any
) -> dict[tuple[str, int], int]:
    totals: defaultdict[tuple[str, int], int] = defaultdict(int)
    chosen = (
        owned.filter(kind="income", part_month=period.month)
        .annotate(
            half=Case(
                When(part=FIRST_PART, then=Value(FIRST)),
                default=Value(SECOND),
                output_field=IntegerField(),
            )
        )
        .values("currency", "half")
        .annotate(total=Sum("amount"))
    )
    for row in chosen:
        totals[(row["currency"], int(row["half"]))] += int(row["total"])
    nearby = (
        owned.filter(
            kind="income",
            part_month__isnull=True,
            date__gte=period.first_day - WINDOW,
            date__lte=period.last_day + WINDOW,
        )
        .values("currency", "date")
        .annotate(total=Sum("amount"))
    )
    for row in nearby:
        month, part = nearest_pay_day(row["date"], pay_days)
        if month == period.month:
            half = FIRST if part == FIRST_PART else SECOND
            totals[(row["currency"], half)] += int(row["total"])
    return totals
