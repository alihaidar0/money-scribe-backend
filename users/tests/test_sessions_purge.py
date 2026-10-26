"""The daily purge of old sessions (ADR 0009): 90 days after a session ended or ran out, and no sooner."""

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.utils import timezone

from users import tasks
from users.models import SecurityEvent, SecurityEventType, Session
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import new_device

pytestmark = pytest.mark.django_db


def make(user: Any, **fields: Any) -> Session:
    now = timezone.now()
    values: dict[str, Any] = {
        "user": user,
        "device_id": new_device()["device_id"],
        "device_name": "Phone",
        "platform": "android",
        "app_version": "1.0",
        "created_at": now - timedelta(days=200),
        "last_active_at": now,
        "rotated_at": now,
        "expires_at": now + timedelta(days=90),
        "refresh_hash": "x" * 64,
    }
    return Session.objects.create(**{**values, **fields})


def test_only_sessions_over_for_more_than_90_days_are_deleted() -> None:
    user = VerifiedUserFactory.create()
    now = timezone.now()
    gone = [
        make(user, ended_at=now - timedelta(days=91), end_reason="signed_out"),
        # Never ended, but its refresh token ran out 95 days ago (rotated 125 days ago).
        make(user, rotated_at=now - timedelta(days=125)),
        # Reached its 90 day limit 100 days ago.
        make(
            user,
            expires_at=now - timedelta(days=100),
            rotated_at=now - timedelta(days=105),
        ),
    ]
    kept = [
        make(user, ended_at=now - timedelta(days=89), end_reason="signed_out"),
        make(user, rotated_at=now - timedelta(days=100)),  # ran out 70 days ago
        make(user),  # live
    ]

    deleted = tasks.purge_old_sessions()

    assert deleted == 3
    assert not Session.objects.filter(pk__in=[s.pk for s in gone]).exists()
    assert Session.objects.filter(pk__in=[s.pk for s in kept]).count() == 3


def test_it_deletes_nothing_when_nothing_is_old_and_is_safe_to_repeat() -> None:
    user = VerifiedUserFactory.create()
    make(user)

    assert tasks.purge_old_sessions() == 0
    assert tasks.purge_old_sessions() == 0
    assert Session.objects.count() == 1


def test_it_leaves_the_security_activity_alone() -> None:
    user = VerifiedUserFactory.create()
    old = make(
        user, ended_at=timezone.now() - timedelta(days=95), end_reason="signed_out"
    )
    SecurityEvent.objects.create(
        user=user,
        type=SecurityEventType.SIGNED_IN,
        occurred_at=timezone.now(),
        session_id=old.id,
        sign_in_method="password",
    )

    tasks.purge_old_sessions()

    assert (
        SecurityEvent.objects.count() == 1
    )  # the entry keeps a plain id of a session that is gone


def test_it_is_scheduled_daily() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["users-purge-sessions"]
    schedule: Any = entry["schedule"]

    assert entry["task"] == "users.tasks.purge_old_sessions"
    assert (schedule.hour, schedule.minute) == ({4}, {27})
