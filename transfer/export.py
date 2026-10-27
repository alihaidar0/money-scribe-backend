"""The CSV export: the person's data written to files, apart from HTTP.

Every file is UTF-8 with a byte order mark (spreadsheet programs then read Arabic correctly), a fixed header row, ``,``
as the separator, ``"`` quoting and CRLF line ends, oldest first, so an import of the file keeps the order within a day.
Dates are ``YYYY-MM-DD``, months ``YYYY-MM`` and amounts decimal strings of the currency's digits (ADR 0008); an empty
cell means "none". A text cell is passed through :func:`transfer.cells.escape_cell`; a JSON cell and every figure never is.
The computed results of a salary month are not exported, because the server recomputes them on import. The accounts and
the custom categories are exported too, so a file can be restored into an empty account; they have no date, so a period
does not apply to them.
"""

import csv
import datetime
import io
import json
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Final

from django.db.models import Prefetch

from common.money import Currency, Money
from debts.models import Debt, Repayment
from ledger.models import Account, Category, Transaction
from salary.models import SalaryMonth
from salary.presentation import ordered
from users.models import User

from .cells import escape_cell
from .units import write_hundredths

BOM: Final = "﻿"
# The order of the files in a ZIP, and the order a person restores them in: a transaction names an account and a
# category, so those two come first.
KINDS: Final = ("accounts", "categories", "transactions", "debts", "salary_months")

HEADERS: Final[dict[str, list[str]]] = {
    "accounts": ["name", "kind"],
    "categories": ["name", "icon"],
    "transactions": [
        "date",
        "kind",
        "amount",
        "currency",
        "category_key",
        "category_name",
        "account_name",
        "note",
    ],
    "debts": [
        "direction",
        "person",
        "amount",
        "currency",
        "start_date",
        "due_date",
        "note",
        "repayments",
    ],
    "salary_months": [
        "month",
        "currency",
        "base_salary",
        "food_per_day",
        "working_days_in_month",
        "working_hours_in_month",
        "extra_hours",
        "tax_rate",
        "payment_mode",
        "working_days_first_half",
        "first_pay_day",
        "second_pay_day",
        "fixed_items",
    ],
}

# The names are fixed: no date and nothing personal.
CSV_NAMES: Final = {kind: f"money-scribe-{kind}.csv" for kind in KINDS}
ZIP_NAME: Final = "money-scribe-export.zip"
ZIP_MEMBERS: Final = {kind: f"{kind}.csv" for kind in KINDS}
# Every member carries the same fixed time, so the archive says when nothing was made.
_ZIP_TIME: Final = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Period:
    """The inclusive days of an export; a side that is ``None`` is open."""

    first: datetime.date | None = None
    last: datetime.date | None = None


@dataclass(frozen=True)
class ExportFile:
    kind: str
    content: bytes
    rows: int


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _money(amount: int, currency: str) -> str:
    return Money(amount, Currency(currency)).to_string()


def _accounts(user: User, period: Period) -> Iterator[list[str]]:
    """Every account of the person, oldest first; a period does not apply (an account has no date)."""
    for account in Account.objects.filter(owner=user).order_by("created_at", "id"):
        yield [escape_cell(account.name), account.kind]


def _categories(user: User, period: Period) -> Iterator[list[str]]:
    """The person's custom categories, oldest first (a built-in one is the same for everyone and is not exported)."""
    for category in Category.objects.filter(owner=user).order_by("created_at", "id"):
        yield [escape_cell(category.name), category.icon]


def _transactions(user: User, period: Period) -> Iterator[list[str]]:
    rows = Transaction.objects.filter(owner=user).select_related("category", "account")
    if period.first is not None:
        rows = rows.filter(date__gte=period.first)
    if period.last is not None:
        rows = rows.filter(date__lte=period.last)
    for item in rows.order_by("date", "created_at", "id").iterator(chunk_size=2000):
        built_in = item.category.owner_id is None
        yield [
            item.date.isoformat(),
            item.kind,
            _money(item.amount, item.currency),
            item.currency,
            (item.category.key or "") if built_in else "",
            "" if built_in else escape_cell(item.category.name),
            escape_cell(item.account.name),
            escape_cell(item.note),
        ]


def _debts(user: User, period: Period) -> Iterator[list[str]]:
    rows = Debt.objects.filter(owner=user).prefetch_related(
        Prefetch("repayments", queryset=Repayment.objects.order_by("date", "id"))
    )
    if period.first is not None:
        rows = rows.filter(start_date__gte=period.first)
    if period.last is not None:
        rows = rows.filter(start_date__lte=period.last)
    for debt in rows.order_by("start_date", "created_at", "id").iterator(
        chunk_size=500
    ):
        yield [
            debt.direction,
            escape_cell(debt.person),
            _money(debt.amount, debt.currency),
            debt.currency,
            debt.start_date.isoformat(),
            debt.due_date.isoformat() if debt.due_date else "",
            escape_cell(debt.note),
            _json(
                [
                    {
                        "date": r.date.isoformat(),
                        "amount": _money(r.amount, debt.currency),
                    }
                    for r in debt.repayments.all()
                ]
            ),
        ]


def _salary_months(user: User, period: Period) -> Iterator[list[str]]:
    rows = SalaryMonth.objects.filter(owner=user).prefetch_related("fixed_items")
    # The period is counted in months: from the month of `from` to the month of `to`.
    if period.first is not None:
        rows = rows.filter(month__gte=period.first.replace(day=1))
    if period.last is not None:
        rows = rows.filter(month__lte=period.last.replace(day=1))
    for month in rows.order_by("month", "id"):
        yield [
            f"{month.month:%Y-%m}",
            month.currency,
            _money(month.base_salary, month.currency),
            _money(month.food_per_day, month.currency),
            str(month.working_days_in_month),
            str(month.working_hours_in_month),
            write_hundredths(month.extra_hours_hundredths),
            write_hundredths(month.tax_rate_basis_points),
            month.payment_mode,
            ""
            if month.working_days_first_half is None
            else str(month.working_days_first_half),
            str(month.first_pay_day),
            str(month.second_pay_day),
            _json(
                [
                    {
                        "name": item.name,
                        "amount": _money(item.amount, month.currency),
                        "kind": item.kind,
                        "part": item.part,
                        # an item that never had a day has no key, so an older file reads the same
                        **({} if item.day is None else {"day": item.day}),
                    }
                    for item in ordered(month.fixed_items.all())
                ]
            ),
        ]


_WRITERS = {
    "accounts": _accounts,
    "categories": _categories,
    "transactions": _transactions,
    "debts": _debts,
    "salary_months": _salary_months,
}


def write_csv(header: list[str], rows: Iterable[list[str]]) -> tuple[bytes, int]:
    """The file as bytes (with the byte order mark) and how many rows it holds besides the header."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(header)
    count = 0
    for row in rows:
        writer.writerow(row)
        count += 1
    return (BOM + buffer.getvalue()).encode("utf-8"), count


def export_files(user: User, kinds: list[str], period: Period) -> list[ExportFile]:
    """One file per kind asked for, always in the order of ``KINDS`` whatever the order of the request."""
    files = []
    for kind in (kind for kind in KINDS if kind in kinds):
        content, count = write_csv(HEADERS[kind], _WRITERS[kind](user, period))
        files.append(ExportFile(kind=kind, content=content, rows=count))
    return files


def zip_files(files: list[ExportFile]) -> bytes:
    """The files in one archive, one member per kind (``transactions.csv``, ...)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            member = zipfile.ZipInfo(ZIP_MEMBERS[file.kind], date_time=_ZIP_TIME)
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o644 << 16
            archive.writestr(member, file.content)
    return buffer.getvalue()
