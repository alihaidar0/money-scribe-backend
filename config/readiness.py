"""``GET /readyz``: do the database and the cache answer? (ADR 0025)

``/healthz`` only proves that the process serves requests: it answers before the database and Redis are touched, and
the platform calls it every few seconds, so a check of the dependencies there would keep a scale-to-zero database awake
all day. This view is the other half. A scheduled workflow calls it now and then, so one request wakes the web service,
the database and the cache and says which of them did not answer.

It is not part of the API: it is outside ``/api/v1/``, it is not in ``openapi/schema.yaml`` and no client calls it. It
does real work per request, so it is protected by a long random token (``READINESS_TOKEN``), sent as
``Authorization: Bearer <token>`` and compared in constant time. An empty token switches the view off: it then answers as
if it did not exist.
"""

import hmac
import logging
from typing import Final

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection
from django.http import Http404, HttpRequest, HttpResponse
from django.views.decorators.http import require_GET
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

CACHE_KEY: Final = "readiness-probe"
CACHE_VALUE: Final = "ok"


def database_answers() -> bool:
    """Run ``SELECT 1``. The reason is logged by class name only: an error text can hold a connection string."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return bool(cursor.fetchone() == (1,))
    except DatabaseError as error:
        logger.warning(
            "readiness: the database did not answer (%s)", type(error).__name__
        )
        return False


def cache_answers() -> bool:
    """Write a value to the cache and read it back."""
    try:
        cache.set(CACHE_KEY, CACHE_VALUE, timeout=60)
        return bool(cache.get(CACHE_KEY) == CACHE_VALUE)
    except (RedisError, OSError) as error:
        logger.warning("readiness: the cache did not answer (%s)", type(error).__name__)
        return False


def bearer_token(request: HttpRequest) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def plain(text: str, status: int) -> HttpResponse:
    response = HttpResponse(text, content_type="text/plain", status=status)
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def readiness(request: HttpRequest) -> HttpResponse:
    expected: str = settings.READINESS_TOKEN
    if not expected:
        raise Http404
    supplied = bearer_token(request)
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        response = plain("", 401)
        response["WWW-Authenticate"] = "Bearer"
        return response
    failed = [
        name
        for name, answers in (("database", database_answers), ("cache", cache_answers))
        if not answers()
    ]
    if failed:
        return plain(", ".join(failed), 503)
    return plain("ok", 200)
