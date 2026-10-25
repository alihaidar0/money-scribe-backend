"""The per-user rate limits by route group (ADR 0021)."""

import logging
from typing import Any

import pytest
from pytest_django.fixtures import Settings
from redis.exceptions import RedisError
from rest_framework.test import APIClient, APIRequestFactory

from config.throttling import DefaultUserThrottle, parse_rate
from users import limits
from users.models import User

BASE = "/api/v1/test"


@pytest.fixture(autouse=True)
def _test_urls(settings: Settings) -> None:
    settings.ROOT_URLCONF = "config.tests.urls"


def rates(settings: Settings, **overrides: str) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            **overrides,
        },
    }


def client_of(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    # The error package fires got_request_exception for every 5xx, which Django's test client would re-raise; a
    # real client just gets the 503 (as in users/tests/test_fail_closed.py).
    client.raise_request_exception = False
    return client


# --- the numbers ------------------------------------------------------------------------------


def test_the_rate_parser_reads_a_count_and_a_period() -> None:
    assert parse_rate("300/min") == (300, 60)
    assert parse_rate("5/hour") == (5, 3600)
    assert parse_rate("10/sec") == (10, 1)
    assert parse_rate("2/day") == (2, 86400)


def test_every_group_has_the_rate_of_adr_0021(settings: Settings) -> None:
    configured = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

    assert {
        group: configured[group]
        for group in (
            "reads",
            "writes",
            "reports",
            "search",
            "export",
            "import",
            "session_actions",
        )
    } == {
        "reads": "300/min",
        "writes": "60/min",
        "reports": "60/min",
        "search": "30/min",
        "export": "5/hour",
        "import": "120/hour",
        "session_actions": "30/hour",
    }


# --- which group a call belongs to ------------------------------------------------------------


class _View:
    pass


def group_of(method: str, path: str = "/x/", **view_attrs: str) -> str | None:
    factory = APIRequestFactory()
    request = getattr(factory, method.lower())(path)
    from rest_framework.request import Request

    view = _View()
    for name, value in view_attrs.items():
        setattr(view, name, value)
    return DefaultUserThrottle().group(Request(request), view)


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_a_safe_call_is_a_read(method: str) -> None:
    assert group_of(method) == "reads"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_call_that_changes_something_is_a_write(method: str) -> None:
    assert group_of(method) == "writes"


def test_a_list_with_a_search_is_a_search_not_a_read() -> None:
    assert group_of("GET", "/x/?search=shop") == "search"
    assert group_of("GET", "/x/?search=") == "reads"


def test_a_view_can_name_its_group() -> None:
    assert group_of("GET", throttle_group="reports") == "reports"
    assert group_of("POST", throttle_group="reports") == "reports"


# --- the limit --------------------------------------------------------------------------------


def test_a_call_over_the_limit_is_429_with_retry_after(
    user: User, settings: Settings
) -> None:
    rates(settings, reads="3/min")
    client = client_of(user)

    answers = [client.get(f"{BASE}/whoami/") for _ in range(4)]

    assert [a.status_code for a in answers] == [200, 200, 200, 429]
    refused = answers[3]
    assert refused.json()["type"] == "client_error"
    assert refused.json()["errors"][0]["code"] == "throttled"
    assert refused.json()["errors"][0]["attr"] is None
    assert 1 <= int(refused["Retry-After"]) <= 60


def test_reads_and_writes_are_counted_apart(user: User, settings: Settings) -> None:
    rates(settings, reads="2/min", writes="2/min")
    client = client_of(user)

    reads = [client.get(f"{BASE}/whoami/").status_code for _ in range(3)]
    writes = [client.post(f"{BASE}/searchable/").status_code for _ in range(3)]

    assert reads == [200, 200, 429]
    assert writes == [200, 200, 429]


def test_a_search_is_counted_only_in_the_search_group(
    user: User, settings: Settings
) -> None:
    rates(settings, reads="1/min", search="3/min")
    client = client_of(user)

    searches = [
        client.get(f"{BASE}/searchable/", {"search": "shop"}).status_code
        for _ in range(4)
    ]
    plain = [client.get(f"{BASE}/searchable/").status_code for _ in range(2)]

    assert searches == [200, 200, 200, 429]
    assert plain == [200, 429]  # the searches did not use the read allowance


def test_a_view_in_another_group_is_counted_there_only(
    user: User, settings: Settings
) -> None:
    rates(settings, reads="1/min", reports="2/min")
    client = client_of(user)

    reports = [client.get(f"{BASE}/report/").status_code for _ in range(3)]
    named = [client.get(f"{BASE}/named-group/").status_code for _ in range(2)]
    read = client.get(f"{BASE}/whoami/").status_code

    assert reports == [200, 200, 429]
    assert named == [429, 429]  # the same group, already used up
    assert read == 200  # reads untouched


def test_the_limit_is_per_user(
    user: User, other_user: User, settings: Settings
) -> None:
    rates(settings, reads="1/min")

    assert client_of(user).get(f"{BASE}/whoami/").status_code == 200
    assert client_of(user).get(f"{BASE}/whoami/").status_code == 429
    assert client_of(other_user).get(f"{BASE}/whoami/").status_code == 200


def test_all_the_devices_of_a_person_share_the_limit(
    user: User, settings: Settings
) -> None:
    rates(settings, reads="2/min")

    answers = [
        client_of(user).get(f"{BASE}/whoami/").status_code for _ in range(3)
    ]  # a new client each time: three devices

    assert answers == [200, 200, 429]


def test_a_call_without_a_signed_in_user_is_not_counted_here(
    api_client: APIClient, settings: Settings
) -> None:
    rates(settings, reads="1/min")

    answers = [api_client.get(f"{BASE}/public/").status_code for _ in range(3)]

    assert answers == [200, 200, 200]


# --- when the limit store is down -------------------------------------------------------------


@pytest.fixture
def store_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RedisError("down")

    monkeypatch.setattr(limits, "count_hit", broken)


def test_reads_and_writes_keep_working_when_the_store_is_down(
    user: User, store_down: None, caplog: pytest.LogCaptureFixture
) -> None:
    client = client_of(user)

    with caplog.at_level(logging.WARNING, logger="config.throttling"):
        read = client.get(f"{BASE}/whoami/")
        write = client.post(f"{BASE}/searchable/")
        search = client.get(f"{BASE}/searchable/", {"search": "x"})
        ending_one = client.delete(f"{BASE}/end-one/")
        report = client.get(f"{BASE}/report/")

    assert [r.status_code for r in (read, write, search, report)] == [200] * 4
    assert ending_one.status_code == 204
    assert "limit is not applied" in caplog.text


@pytest.mark.parametrize("path", ["export", "end-all"])
def test_the_export_and_ending_all_sessions_fail_closed(
    user: User, store_down: None, path: str
) -> None:
    response = client_of(user).post(f"{BASE}/{path}/")

    assert response.status_code == 503
    assert response["Retry-After"] == "30"
    assert response.json() == {
        "type": "server_error",
        "errors": [
            {
                "code": "service_unavailable",
                "detail": "The service is temporarily unavailable. Try again shortly.",
                "attr": None,
            }
        ],
    }


def test_the_export_and_ending_all_sessions_have_their_own_groups(
    user: User, settings: Settings
) -> None:
    rates(settings, export="1/hour", session_actions="2/hour")
    client = client_of(user)

    export = [client.post(f"{BASE}/export/").status_code for _ in range(2)]
    ending = [
        client.delete(f"{BASE}/end-one/").status_code,
        client.post(f"{BASE}/end-all/").status_code,
        client.delete(f"{BASE}/end-one/").status_code,
    ]

    assert export == [200, 429]
    assert ending == [204, 200, 429]  # one shared session-action allowance
