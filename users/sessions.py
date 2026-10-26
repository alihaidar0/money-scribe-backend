"""Which sessions are live, which are over, and the rules that keep their number small (ADR 0009).

A session is **live** when it has not been ended and its refresh token has not expired. The refresh token lives 30
days from its last rotation and never past the session's own 90 day limit (``users.tokens``), so a session nobody
used for 30 days can no longer refresh: it counts as ended with the reason ``expired`` and ``ended_at`` the moment
its token ran out. That is worked out when the list is read; nothing is written for it. A session is shown as ended
for ``SESSION_HISTORY_RETENTION_DAYS`` days after it ended, never longer, even before the daily purge has run.

Two rules keep the list honest (both under a lock on the user's row, so two sign-ins at once cannot pass them):
a sign-in from a device that already has a live session ends the older one (``replaced``), so a device shows once;
and a sign-in at the limit ends the least recently active live session (``session_limit``), and the device it
belonged to is told so with ``session_limit_reached``.
"""

import ipaddress
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db.models import DateTimeField, ExpressionWrapper, F, Q, QuerySet
from django.db.models.functions import Least

from . import events
from .models import EndReason, SecurityEventType, Session, User
from .network import IPV4_PREFIX, IPV6_PREFIX

# The two views of the person's sessions (``status``).
STATUS_CHOICES = [("active", "active"), ("ended", "ended")]


def end_session(
    session: Session, reason: EndReason, now: datetime | None = None
) -> bool:
    """End a session (once; a second call changes nothing). Returns whether this call ended it."""
    from django.utils import timezone

    ended = Session.objects.filter(pk=session.pk, ended_at__isnull=True).update(
        ended_at=now or timezone.now(), end_reason=reason
    )
    return ended > 0


def refresh_expiry() -> Least:
    """The moment a session's refresh token runs out: 30 days after the last rotation, never past the session's limit."""
    lifetime = timedelta(days=settings.REFRESH_TOKEN_LIFETIME_DAYS)
    return Least(
        ExpressionWrapper(F("rotated_at") + lifetime, output_field=DateTimeField()),
        F("expires_at"),
    )


def history_cutoff(now: datetime) -> datetime:
    return now - timedelta(days=settings.SESSION_HISTORY_RETENTION_DAYS)


def live_sessions(user: User, now: datetime) -> QuerySet[Session]:
    """The user's live sessions."""
    return (
        Session.objects.filter(user=user)
        .annotate(refresh_expires_at=refresh_expiry())
        .filter(ended_at__isnull=True, refresh_expires_at__gt=now)
    )


def ended_sessions(user: User, now: datetime) -> QuerySet[Session]:
    """The user's sessions that ended in the last 90 days, newest sign-in first (ordering is the view's)."""
    cutoff = history_cutoff(now)
    return (
        Session.objects.filter(user=user)
        .annotate(refresh_expires_at=refresh_expiry())
        .filter(
            Q(ended_at__isnull=False, ended_at__gt=cutoff)
            | Q(
                ended_at__isnull=True,
                refresh_expires_at__lte=now,
                refresh_expires_at__gt=cutoff,
            )
        )
    )


def network_of(ip: str | None) -> str | None:
    """The stored address as the network it stands for: ``203.0.113.0/24``, or a ``/48`` for IPv6."""
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    prefix = IPV4_PREFIX if address.version == 4 else IPV6_PREFIX
    return str(ipaddress.ip_network((address, prefix), strict=False))


def present(session: Session, *, current_id: uuid.UUID | None) -> SimpleNamespace:
    """A session as the serializer reads it: the end worked out for a session whose refresh token ran out.

    ``session`` comes from a queryset annotated with :func:`refresh_expiry` (a session not so annotated is shown as it is stored).
    """
    ended_at, end_reason = session.ended_at, session.end_reason
    expires = getattr(session, "refresh_expires_at", None)
    if ended_at is None and expires is not None and expires <= _now():
        ended_at, end_reason = expires, EndReason.EXPIRED.value
    return SimpleNamespace(
        id=session.id,
        device_id=session.device_id,
        device_name=session.device_name,
        platform=session.platform,
        os_version=session.os_version,
        app_version=session.app_version,
        created_at=session.created_at,
        last_active_at=session.last_active_at,
        expires_at=session.expires_at,
        is_current=ended_at is None and session.id == current_id,
        sign_in_method=session.sign_in_method,
        network=network_of(session.ip),
        ended_at=ended_at,
        end_reason=end_reason or None,
    )


def _now() -> datetime:
    from django.utils import timezone

    return timezone.now()


def make_room(user: User, device_id: uuid.UUID, now: datetime) -> bool:
    """Make room for a new session of ``device_id``; returns whether this device is new to the account.

    The caller is inside a transaction. The user's row is locked first, so two sign-ins at once see each other.
    An older live session of the same device is ended as ``replaced`` (no event: the person signed in again on the
    same device). Then, at the limit, the least recently active live session ends as ``session_limit`` and a
    ``session_limit_reached`` event names its device.

    A device is *new* when the account has no session of it, in any state, in the retention period (the purge
    deletes a session 90 days after it ended), and the account has signed in before: the very first sign-in is not.
    """
    User.objects.select_for_update().get(pk=user.pk)
    sessions = Session.objects.filter(user=user)
    known_device = sessions.filter(device_id=device_id).exists()
    first_sign_in = not sessions.exists()

    for older in live_sessions(user, now).filter(device_id=device_id):
        end_session(older, EndReason.REPLACED, now)

    live = list(live_sessions(user, now).order_by("last_active_at", "id"))
    while len(live) >= settings.SESSION_MAX_ACTIVE:
        oldest = live.pop(0)
        if end_session(oldest, EndReason.SESSION_LIMIT, now):
            events.record_session_event(
                user, SecurityEventType.SESSION_LIMIT_REACHED, oldest, now=now
            )
    return not known_device and not first_sign_in
