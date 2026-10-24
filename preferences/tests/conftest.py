import pytest
from rest_framework.test import APIClient

from users.models import User
from users.tests.helpers import start_session


@pytest.fixture
def client(api_client: APIClient) -> APIClient:
    """A client that hands back a 5xx response instead of re-raising it."""
    api_client.raise_request_exception = False
    return api_client


@pytest.fixture
def account(client: APIClient) -> tuple[User, str]:
    user, body = start_session(client)
    return user, str(body["access_token"])
