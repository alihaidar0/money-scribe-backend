"""The security events (ADR 0022): one place writes them, always inside the transaction of the action.

An event says what happened to the account and on which device, never a value: no IP address, no user agent, no
amount, no note, no name. If an event cannot be written the action does not happen, because the caller's
transaction rolls back with it. Which device an event carries is fixed by ADR 0022:

* a session event (sign-in, sign-out, password or email change, export, import) carries the device of the session
  where it was done; an event about an *ended* session (token reuse, revoked, limit) carries the ended session's;
* a key event carries the device of the key, and the app version only when the acting session is on that device;
* a password reset has no session, so it has no device.
"""

import uuid
from datetime import datetime
from typing import Any

from django.utils import timezone

from .models import (
    DATA_EVENT_TYPES,
    DeviceKey,
    SecurityEvent,
    SecurityEventType,
    Session,
    SignInMethod,
    User,
)


def _write(
    user: User,
    event_type: SecurityEventType,
    *,
    session_id: uuid.UUID | None = None,
    device_name: str | None = None,
    platform: str | None = None,
    app_version: str | None = None,
    sign_in_method: SignInMethod | None = None,
    kinds: list[str] | None = None,
    count: int | None = None,
    now: datetime | None = None,
) -> SecurityEvent:
    """The one function that creates an event."""
    return SecurityEvent.objects.create(
        user=user,
        type=event_type,
        occurred_at=now or timezone.now(),
        session_id=session_id,
        device_name=device_name,
        platform=platform,
        app_version=app_version,
        sign_in_method=sign_in_method,
        kinds=kinds,
        count=count,
    )


def record_session_event(
    user: User,
    event_type: SecurityEventType,
    session: Session,
    *,
    sign_in_method: SignInMethod | None = None,
    now: datetime | None = None,
) -> SecurityEvent:
    """An event that belongs to a session, with the device of that session."""
    return _write(
        user,
        event_type,
        session_id=session.id,
        device_name=session.device_name,
        platform=session.platform,
        app_version=session.app_version,
        sign_in_method=sign_in_method,
        now=now,
    )


def record_account_event(
    user: User, event_type: SecurityEventType, *, now: datetime | None = None
) -> SecurityEvent:
    """An event with no session and no device (a password reset)."""
    return _write(user, event_type, now=now)


def record_key_event(
    user: User,
    event_type: SecurityEventType,
    key: DeviceKey,
    *,
    acting: Session | None = None,
    now: datetime | None = None,
) -> SecurityEvent:
    """A device key was added or removed: the device of the key, and the session that acted (if any)."""
    on_the_same_device = acting is not None and acting.device_id == key.device_id
    return _write(
        user,
        event_type,
        session_id=acting.id if acting is not None else None,
        device_name=key.device_name,
        platform=key.platform,
        app_version=acting.app_version if on_the_same_device and acting else None,
        now=now,
    )


def record_data_event(
    user: User,
    event_type: SecurityEventType,
    session: Session,
    *,
    kinds: list[str],
    count: int,
    now: datetime | None = None,
) -> SecurityEvent:
    """A CSV export or import: which data and how many rows, never a value."""
    if event_type not in DATA_EVENT_TYPES:
        raise ValueError("Only an export or an import has kinds and a count.")
    extra: dict[str, Any] = {"kinds": kinds, "count": count}
    return _write(
        user,
        event_type,
        session_id=session.id,
        device_name=session.device_name,
        platform=session.platform,
        app_version=session.app_version,
        now=now,
        **extra,
    )
