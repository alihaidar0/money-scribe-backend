from collections.abc import Iterator

import pytest
from django.db import connection
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from idempotency.tests.support import Note


@pytest.fixture(autouse=True)
def note_table(db: None) -> Iterator[None]:
    """The table of the throwaway resource, for the length of one test."""
    with connection.schema_editor() as editor:
        editor.create_model(Note)
    yield
    with connection.cursor() as cursor:
        cursor.execute(
            "SET CONSTRAINTS ALL IMMEDIATE"
        )  # no pending foreign key checks may block the drop
    with connection.schema_editor() as editor:
        editor.delete_model(Note)


@pytest.fixture(autouse=True)
def _urls(settings: Settings) -> None:
    settings.ROOT_URLCONF = "idempotency.tests.urls"


@pytest.fixture
def client(api_client: APIClient) -> APIClient:
    """A client that hands back a 5xx response instead of re-raising it."""
    api_client.raise_request_exception = False
    return api_client
