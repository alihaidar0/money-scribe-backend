"""The calendar days the API accepts: 2000-01-01 to 2099-12-31, the same range for every record with a date."""

import datetime
from typing import Any, Final

from rest_framework.exceptions import ValidationError

from config.error_codes import ErrorCode

FIRST_DAY: Final = datetime.date(2000, 1, 1)
LAST_DAY: Final = datetime.date(2099, 12, 31)


def day_in_range(value: Any) -> Any:
    """A serializer validator: ``400 date_out_of_range`` on the field for a day outside the range."""
    if not FIRST_DAY <= value <= LAST_DAY:
        raise ValidationError(
            f"A day from {FIRST_DAY} to {LAST_DAY}.",
            code=ErrorCode.DATE_OUT_OF_RANGE.value,
        )
    return value
