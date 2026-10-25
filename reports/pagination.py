"""Pagination by day for the day totals (ADR 0003).

A page is ``limit`` **days** (the days that have a record, newest first), so a day always arrives complete, whatever
currencies it holds, and a page never holds more than ``limit`` items. The cursor is the last day of the page; it is the
cursor of every other list (opaque, ``next`` and ``previous``), with the date as the one ordering field, which is unique
per item.
"""

import datetime
from types import SimpleNamespace
from typing import Any

from django.db.models import QuerySet
from rest_framework.request import Request

from config.pagination import _AFTER, _BEFORE, ApiCursorPagination
from ledger.models import Transaction

_FIELDS = [("date", True)]


class DayPagination(ApiCursorPagination):
    """The dates of one page of the day totals; the view sums each of them."""

    def paginate_queryset(
        self, queryset: QuerySet[Transaction], request: Request, view: Any = None
    ) -> list[datetime.date]:
        limit = self._limit(request)
        cursor = self._decode(request, _FIELDS, Transaction)
        days = queryset.order_by().values_list("date", flat=True).distinct()

        if cursor is not None and cursor[0] == _BEFORE:
            rows = list(
                days.filter(date__gt=cursor[1][0]).order_by("date")[: limit + 1]
            )
            page = list(reversed(rows[:limit]))
            has_next = True  # the page we came from is after this one
            has_previous = len(rows) > limit
        else:
            window = days.order_by("-date")
            if cursor is not None:
                window = window.filter(date__lt=cursor[1][0])
            rows = list(window[: limit + 1])
            page = rows[:limit]
            has_next = len(rows) > limit
            has_previous = (
                cursor is not None
                and bool(page)
                and queryset.filter(date__gt=page[0]).exists()
            )

        self._next = self._previous = None
        if page and has_next:
            self._next = self._link(
                request, self._token(SimpleNamespace(date=page[-1]), _FIELDS, _AFTER)
            )
        if page and has_previous:
            self._previous = self._link(
                request, self._token(SimpleNamespace(date=page[0]), _FIELDS, _BEFORE)
            )
        return page
