import datetime
import re
from typing import Final

from django.urls import register_converter
from django.urls.converters import REGISTERED_CONVERTERS

_MONTH: Final = re.compile(r"(20[0-9]{2})-(0[1-9]|1[0-2])")


class MonthConverter:
    """``YYYY-MM`` in the path, the first day of that month in the view; years 2000 to 2099.

    A key that does not fit is not matched by the route, so the answer is the plain ``404`` of a path that does not
    exist, never the JSON body.
    """

    regex = r"20[0-9]{2}-(0[1-9]|1[0-2])"

    def to_python(self, value: str) -> datetime.date:
        match = _MONTH.fullmatch(value)
        if match is None:
            raise ValueError(value)
        return datetime.date(int(match.group(1)), int(match.group(2)), 1)

    def to_url(self, value: datetime.date) -> str:
        return f"{value:%Y-%m}"


def register_month_converter() -> None:
    """Make ``<month:month>`` available in a URL module; safe to call from every module that uses it."""
    if "month" not in REGISTERED_CONVERTERS:
        register_converter(MonthConverter, "month")
