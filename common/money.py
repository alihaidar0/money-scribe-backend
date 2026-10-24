"""Money: integer minor units plus a currency (ADR 0005, ADR 0008).

An amount is never a float. It is a whole number of the currency's smallest unit (kopecks, cents, fils) next to a
currency code, so arithmetic is exact. The rules this module owns:

* **Range.** At most 999 999 999 999 999 minor units in either direction (15 digits, below JavaScript's largest safe
  integer). Anything beyond it, including the result of arithmetic, is an error, never a silent wrap.
* **Rounding.** Half away from zero (``decimal.ROUND_HALF_UP``), applied once, at the end of a formula, on integers
  (:func:`divide_half_up`), never through a float.
* **One currency at a time.** Adding, subtracting or ordering two currencies raises :class:`CurrencyMismatch`; nothing
  is converted. ``==`` between two currencies is simply false.
* **The wire form** (ADR 0008): a decimal string with exactly the currency's fraction digits, ``.`` as the separator,
  no grouping, no exponent, no ``+``, and never ``-0``. :meth:`Money.parse` and :meth:`Money.to_string` are the only
  places that read and write it.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

MAX_MINOR_UNITS: Final = 999_999_999_999_999
# The longest text that can be a valid amount: 15 digits, a point and a sign. Longer input is refused before it is parsed.
_MAX_TEXT_LENGTH: Final = 17

# The shape of an amount on the wire, as the OpenAPI schema states it (ADR 0008). The exact number of fraction
# digits depends on the currency, so :meth:`Money.parse` checks that part.
AMOUNT_PATTERN: Final = r"^(0|[1-9][0-9]*)(\.[0-9]{1,3})?$"
SIGNED_AMOUNT_PATTERN: Final = r"^-?(0|[1-9][0-9]*)(\.[0-9]{1,3})?$"
_SHAPE: Final = re.compile(r"(-?)(0|[1-9][0-9]*)(?:\.([0-9]+))?")


class MoneyError(ValueError):
    """Base of the errors of this module."""


class InvalidAmount(MoneyError):
    """Not a valid amount of this currency (``400 invalid_amount`` at the API)."""


class AmountOutOfRange(InvalidAmount):
    """More than 999 999 999 999 999 minor units, in either direction."""


class CurrencyMismatch(MoneyError):
    """Two amounts of different currencies were combined (``400 currency_mismatch`` at the API)."""


class UnknownCurrency(MoneyError):
    """A currency code that is not supported."""


class Currency(StrEnum):
    """The supported currencies (ISO 4217), each with its number of fraction digits."""

    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    TRY = "TRY"
    SAR = "SAR"
    AED = "AED"
    EGP = "EGP"
    JPY = "JPY"
    KWD = "KWD"

    @property
    def digits(self) -> int:
        """How many digits follow the point: 2 for most, 0 for JPY, 3 for KWD."""
        return _DIGITS[self.value]

    @classmethod
    def parse(cls, code: object) -> Currency:
        """The currency of an ISO code. Only the exact upper-case code is accepted."""
        try:
            if not isinstance(code, str):
                raise ValueError(code)
            return cls(code)
        except ValueError:
            raise UnknownCurrency("The currency is not supported.") from None


_DIGITS: Final = {
    "RUB": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "TRY": 2,
    "SAR": 2,
    "AED": 2,
    "EGP": 2,
    "JPY": 0,
    "KWD": 3,
}


def divide_half_up(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` rounded half away from zero, on integers.

    This is ``Decimal`` ``ROUND_HALF_UP``: 2.5 becomes 3 and -2.5 becomes -3. The denominator must be positive; a
    negative numerator is fine. Every formula that rounds (the salary, the tax) rounds through here, once, last.
    """
    if denominator <= 0:
        raise ValueError("The denominator must be positive.")
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return -quotient if numerator < 0 else quotient


@dataclass(frozen=True, slots=True)
class Money:
    """An amount of one currency, in that currency's minor units. Immutable and always within range."""

    minor: int
    currency: Currency

    def __post_init__(self) -> None:
        if type(self.minor) is not int:
            raise TypeError("Minor units are a whole number.")
        if not isinstance(self.currency, Currency):
            raise TypeError("The currency is a Currency.")
        if abs(self.minor) > MAX_MINOR_UNITS:
            raise AmountOutOfRange("The amount is larger than the largest amount.")

    # --- making one ----------------------------------------------------------------------------

    @classmethod
    def zero(cls, currency: Currency) -> Money:
        return cls(0, currency)

    @classmethod
    def parse(
        cls,
        text: object,
        currency: Currency,
        *,
        signed: bool = False,
        allow_zero: bool = True,
    ) -> Money:
        """Read the wire form (ADR 0008), strictly.

        Exactly the currency's fraction digits (none for JPY, so no point at all), ASCII digits only, no sign
        unless ``signed``, no leading zeros, no ``-0``. ``allow_zero=False`` is for amounts that must be greater
        than zero. Raises :class:`InvalidAmount`; the text is never echoed into the message.
        """
        if not isinstance(text, str) or len(text) > _MAX_TEXT_LENGTH:
            raise InvalidAmount("An amount is a decimal string.")
        shape = _SHAPE.fullmatch(text)
        if shape is None:
            raise InvalidAmount("An amount is a decimal string.")
        sign, whole, fraction = shape.groups()
        if sign and not signed:
            raise InvalidAmount("This amount cannot be negative.")
        fraction = fraction or ""
        if len(fraction) != currency.digits:
            raise InvalidAmount(
                f"{currency.value} has exactly {currency.digits} digits after the point."
            )
        minor = int(whole + fraction)
        if sign and minor == 0:
            raise InvalidAmount("Zero has no sign.")
        if minor == 0 and not allow_zero:
            raise InvalidAmount("The amount must be greater than zero.")
        return cls(-minor if sign else minor, currency)

    @classmethod
    def sum(cls, currency: Currency, items: Iterable[Money]) -> Money:
        """The total of some amounts of one currency (zero for none)."""
        total = cls.zero(currency)
        for item in items:
            total += item
        return total

    # --- writing it ----------------------------------------------------------------------------

    def to_string(self) -> str:
        """The wire form: exactly the currency's fraction digits, ``-`` for a negative amount, never ``-0``."""
        digits = self.currency.digits
        sign = "-" if self.minor < 0 else ""
        whole, fraction = divmod(abs(self.minor), 10**digits)
        if digits == 0:
            return f"{sign}{whole}"
        return f"{sign}{whole}.{fraction:0{digits}d}"

    def to_decimal(self) -> Decimal:
        """The amount as a ``Decimal``, for display or a report; never for arithmetic."""
        return Decimal(self.minor).scaleb(-self.currency.digits)

    # --- what it is ----------------------------------------------------------------------------

    @property
    def is_zero(self) -> bool:
        return self.minor == 0

    @property
    def is_positive(self) -> bool:
        return self.minor > 0

    @property
    def is_negative(self) -> bool:
        return self.minor < 0

    # --- arithmetic ----------------------------------------------------------------------------

    def _same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise CurrencyMismatch("Two currencies cannot be combined.")

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.minor), self.currency)

    def __mul__(self, factor: object) -> Money:
        """Times a whole number, exactly (a daily allowance times the working days)."""
        if type(factor) is not int:
            return NotImplemented
        return Money(self.minor * factor, self.currency)

    __rmul__ = __mul__

    def scaled(self, numerator: int, denominator: int) -> Money:
        """This amount times ``numerator / denominator``, rounded half away from zero, once.

        Use it for a share (a tax rate in basis points is ``scaled(rate, 10000)``). For a formula with several
        steps, multiply the integers first and round last through :func:`divide_half_up`.
        """
        return Money(divide_half_up(self.minor * numerator, denominator), self.currency)

    # --- order ---------------------------------------------------------------------------------

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.minor < other.minor

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.minor <= other.minor

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.minor > other.minor

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.minor >= other.minor
