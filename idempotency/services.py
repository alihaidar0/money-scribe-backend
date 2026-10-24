"""Run a create once, however many times the client repeats it (ADR 0016).

The client sends ``Idempotency-Key: "<random id>"`` once per user action. The first request claims the key by
inserting a row in the **same database transaction** as the create, then stores the answer in it, so the key and the
record appear together or not at all. A repeat finds the row and gets the stored status and body again, with
``Idempotent-Replayed: true``. A request that fails rolls the transaction back and leaves no key, so the client
corrects it and sends it again. Two copies that arrive together meet at the unique index: the second waits for the
first to finish (up to ``IDEMPOTENCY_LOCK_WAIT_SECONDS``, then ``409 idempotency_in_progress``) and then replays it.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Final

from django.conf import settings
from django.db import IntegrityError, OperationalError, connection, transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from config.error_codes import ErrorCode
from config.exceptions import IdempotencyInProgress, IdempotencyKeyReused
from config.preconditions import etag_of

from .models import StoredResponse

HEADER: Final = "Idempotency-Key"
REPLAYED_HEADER: Final = "Idempotent-Replayed"
# PostgreSQL's SQLSTATE for "lock_timeout expired".
LOCK_NOT_AVAILABLE: Final = "55P03"
# An RFC 9651 structured field string: quoted, 16 to 64 letters, digits, hyphens or underscores (a UUID fits).
_KEY: Final = re.compile(r'"([A-Za-z0-9_-]{16,64})"')


def _key_error(code: ErrorCode, detail: str) -> ValidationError:
    return ValidationError({"idempotency_key": [ErrorDetail(detail, code=code.value)]})


def parse_key(value: str | None) -> str:
    """The key of the request, or ``400 idempotency_key_required`` / ``idempotency_key_invalid``."""
    if value is None:
        raise _key_error(
            ErrorCode.IDEMPOTENCY_KEY_REQUIRED,
            "Send an Idempotency-Key header with a random id for this action.",
        )
    match = _KEY.fullmatch(value.strip())
    if match is None:
        raise _key_error(
            ErrorCode.IDEMPOTENCY_KEY_INVALID,
            "The Idempotency-Key is a quoted string of 16 to 64 letters, digits, hyphens or underscores.",
        )
    return match.group(1)


def fingerprint(request: Request) -> str:
    """A hash of what makes two requests the same: the method, the path with its query, and the JSON body.

    The body is read as data and written back in a fixed order, so a retry that orders its keys differently is
    still the same request.
    """
    body = json.dumps(
        request.data,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=True,
    )
    text = f"{request.method}\n{request.get_full_path()}\n{body}"
    return hashlib.sha256(text.encode()).hexdigest()


def _wait_for_lock() -> None:
    """Bound how long a second copy waits for the first (PostgreSQL only; other databases do not block)."""
    if connection.vendor == "postgresql":
        wait = int(settings.IDEMPOTENCY_LOCK_WAIT_SECONDS * 1000)
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", [f"{wait}ms"])


def _stop_waiting_for_locks() -> None:
    """The bound is for the claim only: the create that follows must wait for its own locks as usual."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('lock_timeout', '0', true)")


def _claim(
    user: Any, key: str, digest: str, now: datetime
) -> tuple[StoredResponse, bool]:
    """Take the key, or find the answer that holds it: ``(row, True)`` when this request owns it."""
    existing = (
        StoredResponse.objects.select_for_update().filter(user=user, key=key).first()
    )
    if existing is not None and existing.expires_at <= now:
        existing.delete()
        existing = None
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            created = StoredResponse.objects.create(
                user=user,
                key=key,
                fingerprint=digest,
                created_at=now,
                expires_at=now
                + timedelta(seconds=settings.IDEMPOTENCY_KEY_LIFETIME_SECONDS),
            )
    except IntegrityError:
        # The first copy committed while this one waited for the index: its answer is the one to replay.
        return StoredResponse.objects.select_for_update().get(user=user, key=key), False
    return created, True


def _version_of(body: str) -> int | None:
    """The ``version`` in a stored answer, if it holds one record."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if type(version) is int else None


def _replay(stored: StoredResponse, digest: str) -> HttpResponse:
    if not hmac.compare_digest(stored.fingerprint, digest):
        raise IdempotencyKeyReused()
    response = HttpResponse(
        stored.body, status=stored.status_code, content_type="application/json"
    )
    response[REPLAYED_HEADER] = "true"
    version = _version_of(stored.body)
    if version is not None:
        response["ETag"] = etag_of(version)  # the tag of the record as it was answered
    return response


def run(request: Request, action: Callable[[], Response]) -> Response | HttpResponse:
    """Do ``action`` (a create that returns its ``Response``) at most once for this user and key."""
    key = parse_key(request.headers.get(HEADER))
    digest = fingerprint(request)
    now = timezone.now()
    try:
        with transaction.atomic():
            _wait_for_lock()
            stored, owns = _claim(request.user, key, digest, now)
            _stop_waiting_for_locks()
            if not owns:
                return _replay(stored, digest)
            response = action()
            if 200 <= response.status_code < 300:
                stored.status_code = response.status_code
                stored.body = JSONRenderer().render(response.data).decode()
                stored.save(update_fields=["status_code", "body"])
            else:
                stored.delete()  # only a success is kept
            return response
    except OperationalError as error:
        # The lock wait ran out: the first copy is still running. Any other database error is not ours to hide.
        if getattr(error.__cause__, "sqlstate", None) == LOCK_NOT_AVAILABLE:
            raise IdempotencyInProgress() from None
        raise
