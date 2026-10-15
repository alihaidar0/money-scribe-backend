import pytest
from rest_framework.test import APIClient

from users.models import User
from users.tests.factories import UserFactory


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
