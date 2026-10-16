from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register

from .tokens import MIN_KEY_BYTES


@register(Tags.security)
def check_signing_key(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """``users.E001``: the token signing key must be set and at least 32 bytes long (ADR 0006)."""
    if len(settings.JWT_SIGNING_KEY.encode()) >= MIN_KEY_BYTES:
        return []
    return [
        Error(
            f"JWT_SIGNING_KEY is missing or shorter than {MIN_KEY_BYTES} bytes.",
            hint="Set JWT_SIGNING_KEY to at least 256 random bits, for example `openssl rand -hex 32`.",
            id="users.E001",
        )
    ]
