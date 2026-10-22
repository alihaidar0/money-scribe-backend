"""The bot challenge of sign-in: Cloudflare Turnstile, checked on the server (ADR 0013).

It is demanded only from a client address that has already failed several sign-ins
(:func:`users.limits.challenge_required`), so a person who types their password right never sees one.
The widget only produces a token; the token proves nothing until the server posts it, with the secret
key, to the ``siteverify`` endpoint. A token is single use and lives five minutes.

If the service cannot be reached, or the secret key is refused, the sign-in is answered ``503`` rather
than let through, like the other safety controls (:func:`users.limits.fail_closed`).
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from django.conf import settings
from rest_framework.exceptions import ErrorDetail, ValidationError

from config.error_codes import ErrorCode
from config.exceptions import service_unavailable

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# The widget is created with this action; a token that names another action is refused.
ACTION = "sign_in"
FIELD = "captcha_token"
# Answers that mean "this token is not good"; anything else is our configuration or their outage.
_BAD_TOKEN_ERRORS = {
    "missing-input-response",
    "invalid-input-response",
    "bad-request",
    "timeout-or-duplicate",
}


def is_enabled() -> bool:
    """The challenge exists once a secret key is set; without one (development, tests) it is off."""
    return bool(settings.TURNSTILE_SECRET_KEY)


def _field_error(code: ErrorCode, detail: str) -> ValidationError:
    return ValidationError({FIELD: [ErrorDetail(detail, code=code.value)]})


def required() -> ValidationError:
    """The ``400`` that asks the client to solve the challenge and send ``captcha_token``."""
    return _field_error(
        ErrorCode.CAPTCHA_REQUIRED, "Complete the security check and try again."
    )


def invalid() -> ValidationError:
    """The ``400`` for a token that is wrong, expired or already used."""
    return _field_error(
        ErrorCode.CAPTCHA_INVALID, "The security check failed. Try again."
    )


def _post(fields: dict[str, str]) -> dict[str, Any]:
    """Post to siteverify and return its JSON; raises ``OSError`` or ``ValueError`` if it cannot."""
    request = urllib.request.Request(
        VERIFY_URL,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"User-Agent": "money-scribe-backend"},
    )
    with urllib.request.urlopen(  # noqa: S310 - a fixed https URL
        request, timeout=settings.TURNSTILE_TIMEOUT_SECONDS
    ) as response:
        body: dict[str, Any] = json.loads(response.read().decode())
    return body


def verify(token: str, client_ip: str | None) -> bool:
    """``True`` for a good token of this flow, ``False`` for a bad one; ``503`` if it cannot be decided.

    The retry-safe ``idempotency_key`` is not used: a failed call is answered ``503`` and the client
    asks again with a fresh token.
    """
    fields = {
        "secret": settings.TURNSTILE_SECRET_KEY,
        "response": token,
        "idempotency_key": str(uuid.uuid4()),
    }
    if client_ip:
        fields["remoteip"] = client_ip
    try:
        answer = _post(fields)
    except OSError, ValueError:
        logger.error("The bot challenge service could not be reached.")
        raise service_unavailable() from None
    if answer.get("success") is True:
        # A token made for another flow carries its own action. One made without any (and Cloudflare's
        # dummy test keys) is accepted: the widget of this site key is the sign-in one.
        return answer.get("action") in (None, "", ACTION)
    errors = answer.get("error-codes") or []
    if not set(errors) <= _BAD_TOKEN_ERRORS:
        # A refused secret key or an outage on their side: ours to fix, not the client's fault.
        logger.error("The bot challenge service refused the request: %s", errors)
        raise service_unavailable()
    return False
