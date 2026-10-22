import hashlib
import logging
import unicodedata
import urllib.request
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ErrorDetail

from config.error_codes import ErrorCode

if TYPE_CHECKING:
    from .models import User

logger = logging.getLogger(__name__)

MAX_PASSWORD_LENGTH = 128
PWNED_RANGE_URL = "https://api.pwnedpasswords.com/range/"


def normalize_password(password: str) -> str:
    """Normalize a password to NFKC before it is checked, hashed or compared (NIST SP 800-63B-4).

    The same text typed on two keyboards can differ in its code points; normalizing keeps one password
    one password. Every flow that takes a password goes through this function.
    """
    return unicodedata.normalize("NFKC", password)


def password_errors(password: str, user: User) -> list[ErrorDetail]:
    """Run every configured password rule; each failed rule is its own error with its own code."""
    try:
        validate_password(password, user)
    except ValidationError as error:
        return [
            ErrorDetail(message, code=item.code or "invalid")
            for item in error.error_list
            for message in item.messages
        ]
    return []


class MaximumLengthPasswordValidator:
    """Reject very long passwords: the hashing cost grows with the input, so the length is bounded."""

    def __init__(self, max_length: int = MAX_PASSWORD_LENGTH) -> None:
        self.max_length = max_length

    def validate(self, password: str, user: AbstractBaseUser | None = None) -> None:
        if len(password) > self.max_length:
            raise ValidationError(
                f"The password must be at most {self.max_length} characters.",
                code=ErrorCode.PASSWORD_TOO_LONG.value,
            )

    def get_help_text(self) -> str:
        return f"Your password must be at most {self.max_length} characters."


def breach_count(password: str) -> int | None:
    """How often a password appears in known breaches, or ``None`` if the service cannot be reached.

    Uses the k-anonymity range API of Pwned Passwords: only the first five characters of the SHA-1 of
    the password leave this server, never the password or its full hash. The padding header makes
    every answer the same size, so the response does not hint at the prefix.
    """
    # SHA-1 is what the Pwned Passwords range protocol defines; it is a lookup key here, not how passwords are
    # stored (those use Argon2id), and only its first five characters leave this server.
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    request = urllib.request.Request(  # noqa: S310 - a fixed https URL
        f"{PWNED_RANGE_URL}{prefix}",
        headers={"Add-Padding": "true", "User-Agent": "money-scribe-backend"},
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 - a fixed https URL
            request, timeout=settings.PWNED_PASSWORD_TIMEOUT_SECONDS
        ) as response:
            body = response.read().decode()
    except OSError, ValueError:
        # Never fail a sign-up because a third-party service is down.
        logger.warning("The breached-password check could not be reached.")
        return None
    for line in body.splitlines():
        candidate, _, count = line.partition(":")
        if candidate.strip() == suffix:
            return int(count) if count.strip().isdigit() else 0
    return 0


class CompromisedPasswordValidator:
    """Reject a password that appears in a breach corpus (NIST SP 800-63B-4).

    Skipped when ``PWNED_PASSWORD_CHECK`` is off, and when the service cannot be reached.
    """

    def validate(self, password: str, user: AbstractBaseUser | None = None) -> None:
        if not settings.PWNED_PASSWORD_CHECK:
            return
        if breach_count(password):
            raise ValidationError(
                "This password has appeared in a data breach. Choose another.",
                code=ErrorCode.PASSWORD_COMPROMISED.value,
            )

    def get_help_text(self) -> str:
        return "Your password must not appear in known data breaches."
