"""The readable units of the salary months file: hours and percent with exactly two decimals.

``extra_hours`` is hours (``7.50``, 0.00 to 744.00) and ``tax_rate`` is a percent (``15.00``, 0.00 to 100.00). Both
convert exactly to the stored ``extra_hours_hundredths`` and ``tax_rate_basis_points``: they are whole numbers of
hundredths written with a point, so no floating point is involved and nothing is lost in either direction.
"""

import re
from typing import Final

from salary.models import MAX_EXTRA_HOURS_HUNDREDTHS

MAX_TAX_RATE_BASIS_POINTS: Final = 10000
_TWO_DECIMALS: Final = re.compile(r"(-?)(0|[1-9][0-9]*)\.([0-9]{2})")


class InvalidUnit(ValueError):
    """The text is not a figure with exactly two decimals (``invalid``), or it is out of range."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def hundredths_of(text: object, *, maximum: int) -> int:
    """The whole number of hundredths in ``text`` (``"7.50"`` is 750).

    Raises :class:`InvalidUnit` with the code ``invalid`` (not a string, a comma, more or fewer than two decimals, a
    plus sign, a leading zero), ``min_value`` (below zero) or ``max_value`` (above ``maximum``).
    """
    if not isinstance(text, str) or len(text) > 12:
        raise InvalidUnit("invalid")
    shape = _TWO_DECIMALS.fullmatch(text)
    if shape is None:
        raise InvalidUnit("invalid")
    sign, whole, fraction = shape.groups()
    value = int(whole + fraction)
    if sign and value > 0:
        raise InvalidUnit("min_value")
    if sign:  # "-0.00" has no sign
        raise InvalidUnit("invalid")
    if value > maximum:
        raise InvalidUnit("max_value")
    return value


def hours_to_hundredths(text: object) -> int:
    return hundredths_of(text, maximum=MAX_EXTRA_HOURS_HUNDREDTHS)


def percent_to_basis_points(text: object) -> int:
    return hundredths_of(text, maximum=MAX_TAX_RATE_BASIS_POINTS)


def write_hundredths(value: int) -> str:
    """The figure with exactly two decimals: 750 is ``7.50``."""
    whole, fraction = divmod(value, 100)
    return f"{whole}.{fraction:02d}"
