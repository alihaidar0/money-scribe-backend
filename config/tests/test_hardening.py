"""The baseline of ADR 0021: request id, no-store, nosniff, the size of a body and the CORS headers."""

import logging
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pytest_django.fixtures import Settings
from rest_framework.response import Response
from rest_framework.test import APIClient

from config.logging_filters import RequestIdFilter
from config.middleware import request_id_var
from users.tests.helpers import AUTH, bearer, first_error, start_session

pytestmark = pytest.mark.django_db

PROFILE = f"{AUTH}/profile/"
REQUEST_ID = "X-Request-ID"


@pytest.fixture
def token(api_client: APIClient) -> str:
    _, body = start_session(api_client)
    return str(body["access_token"])


def read_profile(client: APIClient, token: str, **headers: str) -> Response:
    return client.get(PROFILE, headers={**bearer(token), **headers})


# --- the request id ---------------------------------------------------------------------------


def test_every_answer_carries_a_generated_request_id(
    api_client: APIClient, token: str
) -> None:
    response = read_profile(api_client, token)

    assert response.status_code == 200
    assert str(uuid.UUID(response[REQUEST_ID])) == response[REQUEST_ID]


def test_two_requests_get_two_ids(api_client: APIClient, token: str) -> None:
    first = read_profile(api_client, token)
    second = read_profile(api_client, token)

    assert first[REQUEST_ID] != second[REQUEST_ID]


@pytest.mark.parametrize(
    "own",
    ["12345678", "7c1f0d52-3b8e-4a49-9d1e-5b6a2f0c8e11", "A" * 64, "abc-DEF-123-xyz"],
)
def test_a_well_formed_id_of_the_client_is_kept(
    api_client: APIClient, token: str, own: str
) -> None:
    response = read_profile(api_client, token, **{REQUEST_ID: own})

    assert response[REQUEST_ID] == own


@pytest.mark.parametrize(
    "own",
    [
        "",
        "short",
        "A" * 65,
        "has space in it",
        "under_score_not_ok",
        "café-12345",
        "x;y=1,2,3",
    ],
)
def test_a_badly_formed_id_is_replaced_never_refused(
    api_client: APIClient, token: str, own: str
) -> None:
    response = read_profile(api_client, token, **{REQUEST_ID: own})

    assert response.status_code == 200
    assert response[REQUEST_ID] != own
    assert re.fullmatch(r"[0-9a-f-]{36}", response[REQUEST_ID])


def test_an_error_answer_carries_the_id(api_client: APIClient) -> None:
    response = api_client.get(PROFILE)  # no token

    assert response.status_code == 401
    assert response[REQUEST_ID]


def test_a_validation_error_carries_the_id(api_client: APIClient, token: str) -> None:
    response = api_client.patch(
        PROFILE, {"name": "Sam\u202eEvil"}, headers=bearer(token)
    )

    assert response.status_code == 400
    assert response[REQUEST_ID]


def test_a_page_that_is_not_a_route_carries_the_id(
    api_client: APIClient, settings: Settings
) -> None:
    settings.ALLOWED_HOSTS = ["*"]

    response = api_client.get("/api/v1/nothing-here/")

    assert response.status_code == 404
    assert response[REQUEST_ID]


def test_a_refusal_of_the_size_check_carries_the_id(
    api_client: APIClient, token: str, settings: Settings
) -> None:
    settings.REQUEST_BODY_MAX_BYTES = 10

    response = api_client.patch(
        PROFILE, {"name": "A name that is long"}, headers=bearer(token)
    )

    assert response.status_code == 400
    assert response[REQUEST_ID]


def test_the_id_is_in_the_log_lines_of_the_request(
    api_client: APIClient, token: str
) -> None:
    class Collector(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.ids: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.ids.append(str(record.request_id))  # type: ignore[attr-defined]

    handler = Collector()
    handler.addFilter(RequestIdFilter())
    logger = logging.getLogger("django.request")
    logger.addHandler(handler)
    try:
        response = api_client.patch(
            PROFILE, {"name": "Sam\u202eEvil"}, headers=bearer(token)
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 400
    assert handler.ids == [response[REQUEST_ID]]


def test_a_log_line_outside_a_request_shows_a_dash() -> None:
    record = logging.LogRecord("x", logging.INFO, "f.py", 1, "m", None, None)

    assert RequestIdFilter().filter(record)
    assert record.request_id == "-"  # type: ignore[attr-defined]
    token = request_id_var.set("abc-12345")
    try:
        RequestIdFilter().filter(record)
        assert record.request_id == "abc-12345"  # type: ignore[attr-defined]
    finally:
        request_id_var.reset(token)


@pytest.fixture
def web_origin(settings: Settings) -> Iterator[str]:
    origin = "https://app.example.org"
    settings.CORS_ALLOWED_ORIGINS = [origin]
    yield origin


def test_a_browser_may_send_and_read_the_request_id(
    api_client: APIClient, token: str, web_origin: str
) -> None:
    preflight = api_client.options(
        PROFILE,
        headers={
            "Origin": web_origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-request-id,authorization",
        },
    )
    answer = read_profile(api_client, token, Origin=web_origin)

    assert "x-request-id" in preflight["Access-Control-Allow-Headers"].lower()
    exposed = answer["Access-Control-Expose-Headers"].lower()
    assert "x-request-id" in exposed
    assert "retry-after" in exposed


# --- no-store and nosniff ---------------------------------------------------------------------


def test_every_answer_under_the_api_is_no_store(
    api_client: APIClient, token: str, settings: Settings
) -> None:
    settings.ALLOWED_HOSTS = ["*"]
    answers: dict[str, Response] = {
        "success": read_profile(api_client, token),
        "401": api_client.get(PROFILE),
        "400": api_client.patch(PROFILE, {"name": ""}, headers=bearer(token)),
        "404 page": api_client.get("/api/v1/nothing-here/"),
        "public": api_client.post(f"{AUTH}/sign-in/", {}),
    }

    assert {name: r["Cache-Control"] for name, r in answers.items()} == dict.fromkeys(
        answers, "no-store"
    )


def test_a_throttled_answer_is_no_store(
    api_client: APIClient, token: str, settings: Settings
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "reads": "1/min",
        },
    }
    read_profile(api_client, token)

    response = read_profile(api_client, token)

    assert response.status_code == 429
    assert response["Cache-Control"] == "no-store"
    assert int(response["Retry-After"]) >= 1


def test_the_health_check_keeps_its_own_header(api_client: APIClient) -> None:
    response = api_client.get("/healthz")

    assert response["Cache-Control"] == "no-store"


def test_an_api_answer_is_not_content_sniffed(
    api_client: APIClient, token: str
) -> None:
    assert read_profile(api_client, token)["X-Content-Type-Options"] == "nosniff"


# --- the size of a body -----------------------------------------------------------------------


def test_a_body_over_the_limit_is_refused_before_it_is_read(
    api_client: APIClient, token: str, settings: Settings
) -> None:
    settings.REQUEST_BODY_MAX_BYTES = 100

    response = api_client.patch(PROFILE, {"name": "x" * 200}, headers=bearer(token))

    assert response.status_code == 400
    assert response.json() == {
        "type": "client_error",
        "errors": [
            {
                "code": "request_too_large",
                "detail": "The request body is too large.",
                "attr": None,
            }
        ],
    }
    assert response["Cache-Control"] == "no-store"


def test_a_body_exactly_at_the_limit_is_read(
    api_client: APIClient, token: str, settings: Settings
) -> None:
    body = b'{"name": "Sam"}'
    settings.REQUEST_BODY_MAX_BYTES = len(body)

    response = api_client.generic(
        "PATCH", PROFILE, body, content_type="application/json", headers=bearer(token)
    )

    assert response.status_code == 200


def test_the_default_limit_is_256_kb(
    api_client: APIClient, token: str, settings: Settings
) -> None:
    assert settings.REQUEST_BODY_MAX_BYTES == 256 * 1024
    body = b'{"name": "' + b"x" * (256 * 1024) + b'"}'

    response = api_client.generic(
        "PATCH", PROFILE, body, content_type="application/json", headers=bearer(token)
    )

    assert first_error(response)["code"] == "request_too_large"


def test_a_path_can_have_its_own_larger_limit(
    api_client: APIClient, token: str, settings: Settings
) -> None:
    settings.REQUEST_BODY_MAX_BYTES = 10
    settings.REQUEST_BODY_LIMITS = {PROFILE: 100}

    ok = api_client.patch(PROFILE, {"name": "Sam"}, headers=bearer(token))
    too_big = api_client.patch(PROFILE, {"name": "x" * 200}, headers=bearer(token))

    assert ok.status_code == 200
    assert first_error(too_big)["code"] == "request_too_large"


def test_the_csv_import_has_its_own_larger_limit(settings: Settings) -> None:
    assert settings.REQUEST_BODY_LIMITS["/api/v1/imports/"] == 1024 * 1024


def test_a_body_without_a_content_length_is_never_read(
    api_client: APIClient, token: str, settings: Settings
) -> None:
    """A WSGI server gives Django no body at all without a length, so nothing can grow past the limit unseen."""
    settings.REQUEST_BODY_MAX_BYTES = 100
    body = b'{"name": "' + b"x" * 5000 + b'"}'

    response = api_client.generic(
        "PATCH",
        PROFILE,
        body,
        content_type="application/json",
        headers=bearer(token),
        CONTENT_LENGTH="",
    )

    # The body was not read: the view saw an empty request and asked for the missing name.
    assert response.status_code == 400
    error: dict[str, Any] = first_error(response)
    assert (error["code"], error["attr"]) == ("required", "name")


def test_a_content_length_that_is_not_a_number_is_left_to_the_server(
    api_client: APIClient, token: str
) -> None:
    response = api_client.generic(
        "PATCH",
        PROFILE,
        b'{"name": "Sam"}',
        content_type="application/json",
        headers=bearer(token),
        CONTENT_LENGTH="abc",
    )

    assert first_error(response)["code"] != "request_too_large"
