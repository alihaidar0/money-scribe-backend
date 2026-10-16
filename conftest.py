from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from users.models import User
from users.tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    """The lock and limit counters live in the cache; no test may see another test's counts."""
    cache.clear()
    yield
    cache.clear()


class Clock:
    """A clock a test moves by hand: ``clock.advance(seconds=31)``."""

    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Freeze ``django.utils.timezone.now`` at a fixed instant that the test can move."""
    fake = Clock(datetime(2026, 10, 15, 12, 0, tzinfo=UTC))
    monkeypatch.setattr(timezone, "now", fake)
    return fake


@pytest.fixture
def api_client() -> APIClient:
    """An anonymous API client."""
    return APIClient()


@pytest.fixture
def user(db: None) -> User:
    return UserFactory.create()


@pytest.fixture
def other_user(db: None) -> User:
    return UserFactory.create()


@pytest.fixture
def auth_client(user: User) -> APIClient:
    """An API client signed in as ``user``."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client
