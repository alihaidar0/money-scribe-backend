"""``Idempotency-Key`` on a create (ADR 0016): once, however many times it is repeated."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from conftest import Clock
from idempotency.models import StoredResponse
from idempotency.tasks import purge_expired_keys
from idempotency.tests.helpers import KEY, OTHER_KEY, URL, create, detail
from idempotency.tests.support import NoteList
from users.models import User
from users.tests.helpers import bearer, first_error, start_session

from .support import Note

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(client: APIClient) -> tuple[User, str]:
    user, body = start_session(client)
    return user, body["access_token"]


# --- the header -------------------------------------------------------------------------------


def test_a_create_without_the_key_is_a_400_and_creates_nothing(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = create(client, account[1], key=None)

    assert response.status_code == 400
    assert first_error(response) == {
        "code": "idempotency_key_required",
        "detail": first_error(response)["detail"],
        "attr": "idempotency_key",
    }
    assert Note.objects.count() == 0
    assert StoredResponse.objects.count() == 0


@pytest.mark.parametrize(
    "key",
    [
        "8e03978e-40d5-43e8-bc93-6894a57f9324",  # not quoted
        "'8e03978e-40d5-43e8-bc93-6894a57f9324'",  # single quotes
        '""',
        '"short"',
        '"' + "a" * 15 + '"',  # one too short
        '"' + "a" * 65 + '"',  # one too long
        '"has a space inside 0123456"',
        '"under/score/slash/0123456789"',
        '"éééééééééééééééééééé"',
        '"' + "a" * 20 + '"b"',
        '"' + "a" * 20 + '", "' + "b" * 20 + '"',
        "W/" + KEY,
    ],
)
def test_a_key_that_is_not_a_quoted_16_to_64_character_id_is_refused(
    client: APIClient, account: tuple[User, str], key: str
) -> None:
    response = create(client, account[1], key=key)

    assert response.status_code == 400
    assert first_error(response)["code"] == "idempotency_key_invalid"
    assert first_error(response)["attr"] == "idempotency_key"
    assert Note.objects.count() == 0


@pytest.mark.parametrize("length", [16, 64])
def test_the_shortest_and_the_longest_key_are_accepted(
    client: APIClient, account: tuple[User, str], length: int
) -> None:
    key = '"' + "a" * length + '"'

    assert create(client, account[1], key=key).status_code == 201


# --- a repeat ---------------------------------------------------------------------------------


def test_the_first_create_answers_201_with_the_etag_and_is_not_marked_replayed(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = create(client, account[1])

    body = response.json()
    assert response.status_code == 201
    assert response["ETag"] == '"1"'
    assert "Idempotent-Replayed" not in response
    assert body["title"] == "rent"
    assert body["version"] == 1
    assert StoredResponse.objects.get().status_code == 201


def test_a_repeat_returns_the_first_answer_and_creates_nothing_more(
    client: APIClient, account: tuple[User, str]
) -> None:
    first = create(client, account[1])

    again = create(client, account[1])

    assert again.status_code == first.status_code == 201
    assert again.json() == first.json()
    assert again["Idempotent-Replayed"] == "true"
    assert again["Content-Type"].startswith("application/json")
    assert Note.objects.count() == 1
    assert StoredResponse.objects.count() == 1


def test_a_repeat_keeps_answering_the_same_however_often_it_is_sent(
    client: APIClient, account: tuple[User, str]
) -> None:
    first = create(client, account[1])

    answers = [create(client, account[1]) for _ in range(5)]

    assert {a.json()["id"] for a in answers} == {first.json()["id"]}
    assert Note.objects.count() == 1


def test_the_repeat_answers_what_was_stored_even_after_the_record_changed(
    client: APIClient, account: tuple[User, str]
) -> None:
    first = create(client, account[1]).json()
    client.patch(
        detail(first["id"]), {"title": "rent, edited"}, headers=bearer(account[1])
    )

    again = create(client, account[1])

    assert again.json() == first
    assert again["ETag"] == '"1"'


def test_the_order_of_the_keys_of_the_body_does_not_matter(
    client: APIClient, account: tuple[User, str]
) -> None:
    create(client, account[1], {"title": "rent", "note": 1})

    again = create(client, account[1], {"note": 1, "title": "rent"})

    assert again["Idempotent-Replayed"] == "true"
    assert Note.objects.count() == 1


def test_the_same_key_for_another_request_is_a_422_and_creates_nothing(
    client: APIClient, account: tuple[User, str]
) -> None:
    first = create(client, account[1], {"title": "rent"})

    other = create(client, account[1], {"title": "food"})

    assert other.status_code == 422
    assert first_error(other)["code"] == "idempotency_key_reused"
    assert other.json()["type"] == "client_error"
    assert Note.objects.count() == 1
    assert create(client, account[1], {"title": "rent"}).json() == first.json()


def test_the_same_key_for_another_path_is_a_422(
    client: APIClient, account: tuple[User, str]
) -> None:
    create(client, account[1])

    other = create(client, account[1], url=f"{URL}?other=1")

    assert other.status_code == 422
    assert first_error(other)["code"] == "idempotency_key_reused"


def test_a_key_is_the_users_own_so_another_user_never_sees_a_replay(
    client: APIClient, account: tuple[User, str]
) -> None:
    other_client = APIClient()
    other_client.raise_request_exception = False
    _, other_body = start_session(other_client)
    mine = create(client, account[1])

    theirs = create(other_client, other_body["access_token"])

    assert theirs.status_code == 201
    assert "Idempotent-Replayed" not in theirs
    assert theirs.json()["id"] != mine.json()["id"]
    assert Note.objects.count() == 2
    assert StoredResponse.objects.count() == 2


def test_another_key_is_another_create(
    client: APIClient, account: tuple[User, str]
) -> None:
    create(client, account[1], key=KEY)
    create(client, account[1], key=OTHER_KEY)

    assert Note.objects.count() == 2


# --- what is stored ---------------------------------------------------------------------------


def test_a_failed_create_leaves_no_key_so_the_corrected_request_runs(
    client: APIClient, account: tuple[User, str]
) -> None:
    refused = create(client, account[1], {"title": ""})
    assert refused.status_code == 400
    assert StoredResponse.objects.count() == 0

    corrected = create(client, account[1], {"title": "rent"})

    assert corrected.status_code == 201
    assert "Idempotent-Replayed" not in corrected


def test_a_create_that_breaks_after_the_save_rolls_the_record_and_the_key_back(
    client: APIClient,
    account: tuple[User, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: NoteList, serializer: Any) -> None:
        serializer.save(user=self.request.user)
        raise RuntimeError("the server fell over after the insert")

    monkeypatch.setattr(NoteList, "perform_create", broken)
    failed = create(client, account[1])
    assert failed.status_code == 500
    assert Note.objects.count() == 0
    assert StoredResponse.objects.count() == 0

    monkeypatch.undo()
    assert create(client, account[1]).status_code == 201
    assert Note.objects.count() == 1


def test_the_stored_answer_and_the_record_appear_together(
    client: APIClient, account: tuple[User, str]
) -> None:
    created = create(client, account[1]).json()

    stored = StoredResponse.objects.get()

    assert stored.user == account[0]
    assert stored.key == KEY.strip('"')
    assert stored.expires_at - stored.created_at == timedelta(hours=24)
    assert created["id"] in stored.body
    assert len(stored.fingerprint) == 64


# --- how long a key lives ---------------------------------------------------------------------


def test_a_key_is_honoured_for_24_hours(
    client: APIClient, account: tuple[User, str], clock: Clock
) -> None:
    first = create(client, account[1])
    clock.advance(hours=23, minutes=59)

    assert create(client, account[1]).json() == first.json()
    assert Note.objects.count() == 1


def test_an_expired_key_is_ignored_and_the_create_runs_again(
    client: APIClient, account: tuple[User, str], clock: Clock
) -> None:
    first = create(client, account[1])
    clock.advance(hours=24, seconds=1)

    again = create(client, account[1])

    assert again.status_code == 201
    assert "Idempotent-Replayed" not in again
    assert again.json()["id"] != first.json()["id"]
    assert Note.objects.count() == 2
    assert StoredResponse.objects.count() == 1  # the old row was replaced


def test_the_daily_purge_removes_the_answers_that_expired(
    client: APIClient, account: tuple[User, str]
) -> None:
    create(client, account[1], key=KEY)
    create(client, account[1], key=OTHER_KEY)
    StoredResponse.objects.filter(key=KEY.strip('"')).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    assert purge_expired_keys() == 1

    assert list(StoredResponse.objects.values_list("key", flat=True)) == [
        OTHER_KEY.strip('"')
    ]


def test_the_purge_runs_every_day() -> None:
    from celery.schedules import crontab
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE["idempotency-purge"]

    assert entry["task"] == "idempotency.tasks.purge_expired_keys"
    assert entry["schedule"] == crontab(minute=7, hour=4)


def test_deleting_the_account_deletes_the_stored_answers(
    client: APIClient, account: tuple[User, str]
) -> None:
    create(client, account[1])

    account[0].delete()

    assert StoredResponse.objects.count() == 0


# --- what it does not do ----------------------------------------------------------------------


def test_reading_and_editing_need_no_key(
    client: APIClient, account: tuple[User, str]
) -> None:
    note = create(client, account[1]).json()

    listed = client.get(URL, headers=bearer(account[1]))
    edited = client.patch(
        detail(note["id"]), {"title": "x"}, headers=bearer(account[1])
    )

    assert listed.status_code == edited.status_code == 200


def test_a_create_needs_a_signed_in_user(client: APIClient) -> None:
    response = client.post(URL, {"title": "rent"}, headers={"Idempotency-Key": KEY})

    assert response.status_code == 401


def test_the_body_of_a_request_never_reaches_a_log(
    client: APIClient, account: tuple[User, str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG"):
        create(client, account[1], {"title": "a-private-title-1234"})
        create(client, account[1], {"title": "a-private-title-1234"})
        create(client, account[1], {"title": "another-private-5678"})

    assert "private" not in caplog.text
