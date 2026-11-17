"""``GET /readyz``: the check that the database and the cache answer (ADR 0025)."""

import logging
from collections.abc import Iterator

import pytest
from django.core.cache import cache
from django.db import OperationalError
from pytest_django.fixtures import Settings
from rest_framework.response import Response
from rest_framework.test import APIClient

from config import readiness

pytestmark = pytest.mark.django_db

TOKEN = "test-only-readiness-token-0123456789abcdef0123456789abcdef"  # invented, not a real secret


@pytest.fixture(autouse=True)
def enabled(settings: Settings) -> Iterator[None]:
    settings.READINESS_TOKEN = TOKEN
    yield


def ask(client: APIClient, token: str | None = TOKEN) -> Response:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token is not None else {}
    return client.get("/readyz", **headers)


def test_ready_when_the_database_and_the_cache_answer(api_client: APIClient) -> None:
    response = ask(api_client)

    assert response.status_code == 200
    assert response.content == b"ok"
    assert response["Cache-Control"] == "no-store"
    assert response["Content-Type"].startswith("text/plain")


def test_the_check_writes_and_reads_the_cache(api_client: APIClient) -> None:
    cache.delete(readiness.CACHE_KEY)

    ask(api_client)

    assert cache.get(readiness.CACHE_KEY) == readiness.CACHE_VALUE


def test_it_does_not_exist_while_the_token_is_empty(
    api_client: APIClient, settings: Settings
) -> None:
    settings.READINESS_TOKEN = ""

    assert ask(api_client, TOKEN).status_code == 404
    assert ask(api_client, "").status_code == 404
    assert ask(api_client, None).status_code == 404


@pytest.mark.parametrize("supplied", [None, "", "wrong", TOKEN + "x", TOKEN[:-1]])
def test_a_missing_or_wrong_token_is_refused(
    api_client: APIClient, supplied: str | None
) -> None:
    response = ask(api_client, supplied)

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert response["Cache-Control"] == "no-store"
    assert response.content == b""


def test_the_token_is_not_accepted_in_the_query_string(api_client: APIClient) -> None:
    response = api_client.get("/readyz", {"token": TOKEN})

    assert response.status_code == 401


def test_only_get_is_allowed(api_client: APIClient) -> None:
    response = api_client.post(
        "/readyz", HTTP_AUTHORIZATION=f"Bearer {TOKEN}", format="json"
    )

    assert response.status_code == 405


class BrokenDatabase:
    def cursor(self) -> object:
        raise OperationalError(
            "connection to server at 10.0.0.1 failed: password=hunter2"
        )


class BrokenCache:
    def set(self, *args: object, **kwargs: object) -> None:
        raise ConnectionError("Error 111 connecting to redis://:hunter2@10.0.0.2:6379")

    def get(self, *args: object, **kwargs: object) -> None:
        raise ConnectionError("unreachable")


class ForgetfulCache:
    """Takes the write and does not give it back."""

    def set(self, *args: object, **kwargs: object) -> None:
        return None

    def get(self, *args: object, **kwargs: object) -> None:
        return None


def test_a_database_that_does_not_answer_is_a_503(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "connection", BrokenDatabase())

    response = ask(api_client)

    assert response.status_code == 503
    assert response.content == b"database"


def test_a_cache_that_does_not_answer_is_a_503(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "cache", BrokenCache())

    response = ask(api_client)

    assert response.status_code == 503
    assert response.content == b"cache"


def test_a_cache_that_forgets_the_value_is_a_503(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "cache", ForgetfulCache())

    assert ask(api_client).status_code == 503


def test_both_failing_are_named(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness, "connection", BrokenDatabase())
    monkeypatch.setattr(readiness, "cache", ForgetfulCache())

    response = ask(api_client)

    assert response.status_code == 503
    assert response.content == b"database, cache"


def test_a_failure_is_logged_by_class_without_the_error_text(
    api_client: APIClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(readiness, "connection", BrokenDatabase())
    monkeypatch.setattr(readiness, "cache", BrokenCache())

    with caplog.at_level(logging.WARNING, logger=readiness.__name__):
        ask(api_client)

    assert "OperationalError" in caplog.text
    assert "ConnectionError" in caplog.text
    assert "hunter2" not in caplog.text
    assert "10.0.0" not in caplog.text
    assert TOKEN not in caplog.text
