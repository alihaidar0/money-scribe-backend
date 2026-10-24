"""Two requests at once: one create, and one winner (ADR 0016). These run real threads on real transactions."""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

import pytest
from django.db import connections
from rest_framework.test import APIClient

from idempotency.models import StoredResponse
from idempotency.tests.helpers import KEY, URL, create, detail
from idempotency.tests.support import SLOW, Note
from users.tests.helpers import bearer, start_session

pytestmark = pytest.mark.django_db(transaction=True)


class Answer(NamedTuple):
    status: int
    body: Any
    headers: dict[str, str]


def call(
    method: str, url: str, token: str, body: dict[str, Any], **headers: str
) -> Answer:
    """One request from its own client and its own database connection, as a second device would make it."""
    client = APIClient()
    client.raise_request_exception = False
    try:
        response = getattr(client, method)(
            url, body, headers={**bearer(token), **headers}
        )
        return Answer(response.status_code, response.json(), dict(response.headers))
    finally:
        connections.close_all()


@pytest.fixture
def token() -> str:
    _, body = start_session(APIClient())
    return str(body["access_token"])


def test_two_copies_that_arrive_together_create_once_and_both_get_the_answer(
    token: str,
) -> None:
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(
            call, "post", URL, token, {"title": SLOW}, **{"Idempotency-Key": KEY}
        )
        time.sleep(0.4)  # the first has claimed the key and is holding its transaction
        second = pool.submit(
            call, "post", URL, token, {"title": SLOW}, **{"Idempotency-Key": KEY}
        )
        answers = [first.result(), second.result()]

    assert [a.status for a in answers] == [201, 201]
    assert answers[0].body == answers[1].body
    assert [("Idempotent-Replayed" in a.headers) for a in answers] == [False, True]
    assert Note.objects.count() == 1
    assert StoredResponse.objects.count() == 1


def test_a_copy_that_cannot_wait_for_the_first_is_a_409_and_a_retry_replays(
    token: str, settings: Any
) -> None:
    settings.IDEMPOTENCY_LOCK_WAIT_SECONDS = 0.3
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(
            call, "post", URL, token, {"title": SLOW}, **{"Idempotency-Key": KEY}
        )
        time.sleep(0.4)
        second = pool.submit(
            call, "post", URL, token, {"title": SLOW}, **{"Idempotency-Key": KEY}
        )
        busy, done = second.result(), first.result()

    assert done.status == 201
    assert busy.status == 409
    assert busy.body["errors"][0]["code"] == "idempotency_in_progress"
    assert busy.body["errors"][0]["attr"] is None
    assert int(busy.headers["Retry-After"]) >= 1
    assert Note.objects.count() == 1

    retry = call("post", URL, token, {"title": SLOW}, **{"Idempotency-Key": KEY})
    assert retry.status == 201
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.body == done.body
    assert Note.objects.count() == 1


def test_two_edits_from_the_same_version_have_one_winner(token: str) -> None:
    client = APIClient()
    note = create(client, token).json()
    headers = {"If-Match": '"1"'}

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(
            call, "patch", detail(note["id"]), token, {"title": SLOW}, **headers
        )
        time.sleep(0.4)  # the first holds the row
        second = pool.submit(
            call, "patch", detail(note["id"]), token, {"title": "other"}, **headers
        )
        answers = [first.result(), second.result()]

    assert [a.status for a in answers] == [200, 412]
    stored = Note.objects.get()
    assert (stored.title, stored.version) == (SLOW, 2)


def test_without_if_match_two_edits_both_apply_and_each_counts(token: str) -> None:
    client = APIClient()
    note = create(client, token).json()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(call, "patch", detail(note["id"]), token, {"title": SLOW})
        time.sleep(0.4)
        second = pool.submit(
            call, "patch", detail(note["id"]), token, {"title": "later"}
        )
        answers = [first.result(), second.result()]

    assert [a.status for a in answers] == [200, 200]
    stored = Note.objects.get()
    assert (stored.title, stored.version) == ("later", 3)


def test_a_delete_from_a_stale_version_loses_to_an_edit_that_got_there_first(
    token: str,
) -> None:
    client = APIClient()
    note = create(client, token).json()

    with ThreadPoolExecutor(2) as pool:
        edit = pool.submit(call, "patch", detail(note["id"]), token, {"title": SLOW})
        time.sleep(0.4)
        delete = pool.submit(
            call, "delete", detail(note["id"]), token, {}, **{"If-Match": '"1"'}
        )
        answers = [edit.result(), delete.result()]

    assert [a.status for a in answers] == [200, 412]
    assert Note.objects.count() == 1
