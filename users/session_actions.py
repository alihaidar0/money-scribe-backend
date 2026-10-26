"""Ending sessions on the person's request (ADR 0009, ADR 0018, ADR 0022).

Both actions run in one transaction under a lock on the user's row. Ending another session records
``session_revoked`` (it carries the device of the session that was ended) and removes the fingerprint key of that
session's device, so a phone the person ended cannot sign itself in again; ending the **current** session is a
sign-out and leaves the key (that is what a key is for). A push token of an ended session goes with it once the
notifications exist: every place that ends a session goes through :func:`users.sessions.end_session`.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from . import events, services, sessions
from .models import DeviceKey, EndReason, SecurityEventType, Session, User


@dataclass(frozen=True)
class Ended:
    ended: int
    device_keys_removed: int


def _remove_keys(
    user: User, keys: list[DeviceKey], acting: Session, now: datetime
) -> int:
    for key in keys:
        events.record_key_event(
            user,
            SecurityEventType.DEVICE_KEY_REMOVED,
            key,
            acting=acting,
            now=now,
        )
    DeviceKey.objects.filter(pk__in=[key.pk for key in keys]).delete()
    return len(keys)


def end_one(*, user: User, current: Session, session_id: uuid.UUID) -> None:
    """End one of the person's own sessions; another person's, or one that does not exist, is ``404``.

    A session that is already over (ended, or its refresh token ran out) is not an error and nothing changes.
    """
    now = timezone.now()
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        target = sessions.live_sessions(user, now).filter(pk=session_id).first()
        if target is None:
            if not Session.objects.filter(user=user, pk=session_id).exists():
                raise NotFound()
            return
        if target.pk == current.pk:
            services.sign_out(current)  # a sign-out: the key stays
            return
        if sessions.end_session(target, EndReason.REVOKED_BY_USER, now):
            events.record_session_event(
                user, SecurityEventType.SESSION_REVOKED, target, now=now
            )
        if target.device_id != current.device_id:
            keys = list(
                DeviceKey.objects.select_for_update().filter(
                    user=user, device_id=target.device_id
                )
            )
            _remove_keys(user, keys, current, now)


def end_others(*, user: User, current: Session, password: str) -> Ended:
    """End every other live session and remove every device key but the current device's.

    The password is asked for again, with the lock shared with the password change. The keys of phones that are
    signed out go too: the person is answering "someone else may have my account".
    """
    services.confirm_password(user, password, "current_password")
    now = timezone.now()
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        ended = 0
        for other in sessions.live_sessions(user, now).exclude(pk=current.pk):
            if sessions.end_session(other, EndReason.REVOKED_BY_USER, now):
                events.record_session_event(
                    user, SecurityEventType.SESSION_REVOKED, other, now=now
                )
                ended += 1
        keys = list(
            DeviceKey.objects.select_for_update()
            .filter(user=user)
            .exclude(device_id=current.device_id)
        )
        removed = _remove_keys(user, keys, current, now)
    return Ended(ended=ended, device_keys_removed=removed)
