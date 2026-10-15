from collections.abc import Iterable
from typing import Any

from rest_framework.test import APIClient

WRITE_METHODS = {"put", "patch", "post"}


def assert_hidden_from_other_users(
    client: APIClient,
    url: str,
    methods: Iterable[str] = ("get", "put", "patch", "delete"),
) -> None:
    """Assert that another user's object answers ``404`` for every method.

    ``client`` must be signed in as a user who does NOT own the object at ``url``. A user can never
    read or write another user's rows, and the answer is ``404``, never ``403``, so the object's
    existence is not revealed. Call this once per endpoint that serves user data.
    """
    for method in methods:
        data: dict[str, Any] | None = {} if method in WRITE_METHODS else None
        response = getattr(client, method)(url, data=data)
        assert response.status_code == 404, (
            f"{method.upper()} {url} answered {response.status_code}, expected 404"
        )
