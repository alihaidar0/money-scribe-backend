from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, Warning, register

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


@register(Tags.security)
def check_challenge_key(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """``users.W001``: staging and production should have the sign-in bot challenge switched on (ADR 0013)."""
    if settings.TURNSTILE_SECRET_KEY or settings.APP_ENVIRONMENT == "development":
        return []
    return [
        Warning(
            "TURNSTILE_SECRET_KEY is not set, so sign-in has no bot challenge.",
            hint="Create a Cloudflare Turnstile widget and set its secret key (docs/staging.md).",
            id="users.W001",
        )
    ]


@register(Tags.security)
def check_device_key_attestation(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """``users.E002``: only development may skip the Android key attestation (ADR 0018)."""
    if (
        settings.DEVICE_KEY_REQUIRE_ATTESTATION
        or settings.APP_ENVIRONMENT == "development"
    ):
        return []
    return [
        Error(
            "DEVICE_KEY_REQUIRE_ATTESTATION is off outside development.",
            hint="Remove it, or set it to True: an unattested Android key must never be accepted here.",
            id="users.E002",
        )
    ]


@register(Tags.security)
def check_device_key_app(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """``users.W002``: staging and production must say which Android app may register a key (ADR 0018)."""
    if (
        settings.APP_ENVIRONMENT == "development"
        or not settings.DEVICE_KEY_REQUIRE_ATTESTATION
        or (
            settings.DEVICE_KEY_ANDROID_PACKAGE
            and settings.DEVICE_KEY_ANDROID_CERT_SHA256
        )
    ):
        return []
    return [
        Warning(
            "DEVICE_KEY_ANDROID_PACKAGE or DEVICE_KEY_ANDROID_CERT_SHA256 is not set, so no Android key can be registered.",
            hint="Set the package name and the SHA-256 digests of the signing certificates (docs/staging.md).",
            id="users.W002",
        )
    ]
