from typing import Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register

OUTBOX_BACKEND = "outbox.backends.DatabaseEmailBackend"
RESEND_BACKEND = "anymail.backends.resend.EmailBackend"
ENVIRONMENTS = {"development", "staging", "production"}


@register(Tags.security)
def check_outbox_environment(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Keep the database outbox out of production (ADR 0010).

    The outbox stores emails, including verification codes, in readable form. The name of the environment
    is checked too: a typo such as ``prod`` would otherwise slip past the guard.
    """
    errors: list[CheckMessage] = []
    if settings.APP_ENVIRONMENT not in ENVIRONMENTS:
        errors.append(
            Error(
                f"APP_ENVIRONMENT is {settings.APP_ENVIRONMENT!r}.",
                hint="Use one of: development, staging, production.",
                id="outbox.E002",
            )
        )
    elif (
        settings.APP_ENVIRONMENT == "production"
        and settings.EMAIL_BACKEND == OUTBOX_BACKEND
    ):
        errors.append(
            Error(
                "The database email outbox is configured in production.",
                hint="Set EMAIL_BACKEND to a real email backend; the outbox is for development and staging only.",
                id="outbox.E001",
            )
        )
    return errors


@register(Tags.security)
def check_resend_key(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Refuse the Resend backend without an API key (ADR 0024).

    A failed send is only logged (``users.emails``), so a missing key would otherwise show up as codes that never
    arrive.
    """
    if settings.EMAIL_BACKEND != RESEND_BACKEND:
        return []
    if settings.ANYMAIL.get("RESEND_API_KEY"):
        return []
    return [
        Error(
            "EMAIL_BACKEND is the Resend backend but RESEND_API_KEY is empty.",
            hint="Set RESEND_API_KEY in the environment, or choose another EMAIL_BACKEND.",
            id="outbox.E003",
        )
    ]
