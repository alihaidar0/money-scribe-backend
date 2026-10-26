"""Sign-ins at the same moment cannot pass the limit of 10 live sessions, or leave a device twice (ADR 0009)."""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from django.db import connections
from rest_framework.test import APIClient

from users import sessions
from users.models import EndReason, Session, User
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import PASSWORD, device, new_device

pytestmark = pytest.mark.django_db(transaction=True)


class Delayed:
    """A queryset that reads its rows and only then waits: what it read is stale when the caller acts on it."""

    def __init__(self, queryset: Any, seconds: float) -> None:
        self.queryset = queryset
        self.seconds = seconds

    def filter(self, *args: Any, **kwargs: Any) -> Delayed:
        return Delayed(self.queryset.filter(*args, **kwargs), self.seconds)

    def order_by(self, *args: Any) -> Delayed:
        return Delayed(self.queryset.order_by(*args), self.seconds)

    def __iter__(self) -> Any:
        rows = list(self.queryset)
        time.sleep(self.seconds)
        return iter(rows)


def slowed(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.4) -> None:
    """Make the room-making step wait after it has counted, so the sign-ins really overlap.

    Without the lock on the user's row each of them would count the same live sessions, and none would end one.
    """
    original = sessions.live_sessions

    def slow(*args: Any, **kwargs: Any) -> Delayed:
        return Delayed(original(*args, **kwargs), seconds)

    monkeypatch.setattr(sessions, "live_sessions", slow)


def sign_in_at_once(email: str, phone: dict[str, Any]) -> int:
    client = APIClient()
    client.raise_request_exception = False
    try:
        return int(
            client.post(
                "/api/v1/auth/sign-in/",
                {"email": email, "password": PASSWORD, "device": phone},
            ).status_code
        )
    finally:
        connections.close_all()


def live(user: User) -> int:
    return Session.objects.filter(user=user, ended_at__isnull=True).count()


def test_sign_ins_at_the_limit_at_the_same_moment_leave_exactly_ten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = VerifiedUserFactory.create()
    client = APIClient()
    for _ in range(9):
        assert (
            client.post(
                "/api/v1/auth/sign-in/",
                {"email": user.email, "password": PASSWORD, "device": new_device()},
            ).status_code
            == 200
        )
    slowed(monkeypatch)

    with ThreadPoolExecutor(4) as pool:
        futures = []
        for _ in range(4):
            futures.append(pool.submit(sign_in_at_once, user.email, new_device()))
            time.sleep(0.05)
        codes = [f.result() for f in futures]

    assert codes == [200, 200, 200, 200]
    assert live(user) == 10
    assert (
        Session.objects.filter(user=user, end_reason=EndReason.SESSION_LIMIT).count()
        == 3
    )


def test_two_sign_ins_of_one_device_at_the_same_moment_leave_one_live_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = VerifiedUserFactory.create()
    phone = device()
    slowed(monkeypatch)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(sign_in_at_once, user.email, phone)
        time.sleep(0.1)
        second = pool.submit(sign_in_at_once, user.email, phone)
        codes = [first.result(), second.result()]

    assert codes == [200, 200]
    assert live(user) == 1
    assert Session.objects.filter(user=user, end_reason=EndReason.REPLACED).count() == 1
