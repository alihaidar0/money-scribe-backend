"""``version``, ``ETag`` and ``If-Match`` on an edit or a delete (ADR 0016)."""

from typing import Any

import pytest
from rest_framework.test import APIClient

from config.tests.isolation import assert_hidden_from_other_users
from idempotency.tests.helpers import URL, create, detail
from idempotency.tests.support import Note
from users.models import User
from users.tests.helpers import bearer, first_error, start_session

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(client: APIClient) -> tuple[User, str]:
    user, body = start_session(client)
    return user, body["access_token"]


@pytest.fixture
def note(client: APIClient, account: tuple[User, str]) -> dict[str, Any]:
    created: dict[str, Any] = create(client, account[1]).json()
    return created


def patch(
    client: APIClient, token: str, note_id: str, title: str, match: str | None = None
) -> Any:
    headers = dict(bearer(token))
    if match is not None:
        headers["If-Match"] = match
    return client.patch(detail(note_id), {"title": title}, headers=headers)


# --- the model --------------------------------------------------------------------------------


def test_a_new_record_is_version_one_with_both_timestamps(
    account: tuple[User, str],
) -> None:
    created = Note.objects.create(owner_id=account[0].pk, title="a")

    assert created.version == 1
    assert created.created_at is not None
    assert created.updated_at >= created.created_at


def test_every_save_raises_the_version_by_one_and_refreshes_the_instance(
    account: tuple[User, str],
) -> None:
    record = Note.objects.create(owner_id=account[0].pk, title="a")
    first = record.updated_at

    record.title = "b"
    record.save()

    assert record.version == 2
    assert record.updated_at > first
    assert Note.objects.get(pk=record.pk).version == 2


def test_two_stale_copies_saved_in_turn_count_two_changes(
    account: tuple[User, str],
) -> None:
    """The bump is ``version = version + 1`` in the statement, so a stale in-memory copy cannot lose one."""
    record = Note.objects.create(owner_id=account[0].pk, title="a")
    first = Note.objects.get(pk=record.pk)
    second = Note.objects.get(pk=record.pk)

    first.title = "b"
    first.save()
    second.title = "c"
    second.save()

    assert Note.objects.get(pk=record.pk).version == 3


def test_a_save_with_update_fields_still_raises_the_version_and_the_time(
    account: tuple[User, str],
) -> None:
    record = Note.objects.create(owner_id=account[0].pk, title="a")
    before = record.updated_at

    record.title = "b"
    record.save(update_fields=["title"])

    stored = Note.objects.get(pk=record.pk)
    assert (stored.version, stored.title) == (2, "b")
    assert stored.updated_at > before


# --- reading ----------------------------------------------------------------------------------


def test_one_record_answers_its_etag_and_the_list_carries_the_versions(
    client: APIClient, account: tuple[User, str], note: dict[str, Any]
) -> None:
    one = client.get(detail(note["id"]), headers=bearer(account[1]))
    listed = client.get(URL, headers=bearer(account[1]))

    assert one["ETag"] == '"1"'
    assert one.json()["version"] == 1
    assert "ETag" not in listed
    assert [item["version"] for item in listed.json()["results"]] == [1]


def test_the_version_cannot_be_set_from_the_request(
    client: APIClient, account: tuple[User, str], note: dict[str, Any]
) -> None:
    response = client.patch(
        detail(note["id"]),
        {"title": "x", "version": 99},
        headers=bearer(account[1]),
    )

    assert response.json()["version"] == 2


# --- editing ----------------------------------------------------------------------------------


def test_an_edit_without_if_match_is_last_write_wins_and_raises_the_etag(
    client: APIClient, account: tuple[User, str], note: dict[str, Any]
) -> None:
    patch(client, account[1], note["id"], "first")

    response = patch(client, account[1], note["id"], "second")

    assert response.status_code == 200
    assert response.json()["title"] == "second"
    assert response.json()["version"] == 3
    assert response["ETag"] == '"3"'


def test_an_edit_made_from_the_current_version_is_accepted(
    client: APIClient, account: tuple[User, str], note: dict[str, Any]
) -> None:
    response = patch(client, account[1], note["id"], "new", match='"1"')

    assert response.status_code == 200
    assert response["ETag"] == '"2"'


def test_an_edit_made_from_a_stale_version_is_a_412_and_changes_nothing(
    client: APIClient, account: tuple[User, str], note: dict[str, Any]
) -> None:
    patch(client, account[1], note["id"], "from the other device")

    stale = patch(client, account[1], note["id"], "from this one", match='"1"')

    assert stale.status_code == 412
    assert stale.json() == {
        "type": "client_error",
        "errors": [
            {
                "code": "precondition_failed",
                "detail": first_error(stale)["detail"],
                "attr": None,
            }
        ],
    }
    stored = Note.objects.get()
    assert (stored.title, stored.version) == ("from the other device", 2)


@pytest.mark.parametrize(
    "header", ['"1"', "*", '"7", "1"', '"1" , "9"', '"1",*'], ids=str
)
def test_these_headers_match_version_one(
    client: APIClient, account: tuple[User, str], note: dict[str, Any], header: str
) -> None:
    assert patch(client, account[1], note["id"], "x", match=header).status_code == 200


@pytest.mark.parametrize(
    "header",
    [
        '"2"',
        '"01"',
        'W/"1"',
        "1",
        '""',
        "abc",
        '"1',
        '"-1"',
        '"1.0"',
        '"99999999999"',
        ",",
    ],
    ids=str,
)
def test_these_headers_never_match_version_one(
    client: APIClient, account: tuple[User, str], note: dict[str, Any], header: str
) -> None:
    response = patch(client, account[1], note["id"], "x", match=header)

    assert response.status_code == 412
    assert Note.objects.get().version == 1


def test_a_replacement_honours_if_match_too(
    client: APIClient, account: tuple[User, str], note: dict[str, Any]
) -> None:
    stale = client.put(
        detail(note["id"]),
        {"title": "y"},
        headers={**bearer(account[1]), "If-Match": '"5"'},
    )
    fresh = client.put(
        detail(note["id"]),
        {"title": "y"},
        headers={**bearer(account[1]), "If-Match": '"1"'},
    )

    assert (stale.status_code, fresh.status_code) == (412, 200)


def test_an_edit_of_a_note_that_is_not_there_is_a_404_not_a_412(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = patch(
        client, account[1], "0192a000-0000-7000-8000-000000000001", "x", match='"1"'
    )

    assert response.status_code == 404


def test_an_invalid_edit_is_a_400_and_does_not_raise_the_version(
    client: APIClient, account: tuple[User, str], note: dict[str, Any]
) -> None:
    response = patch(client, account[1], note["id"], "", match='"1"')

    assert response.status_code == 400
    assert Note.objects.get().version == 1


# --- deleting ---------------------------------------------------------------------------------


def delete(
    client: APIClient, token: str, note_id: str, match: str | None = None
) -> Any:
    headers = dict(bearer(token))
    if match is not None:
        headers["If-Match"] = match
    return client.delete(detail(note_id), headers=headers)


def test_a_delete_from_a_stale_version_is_a_412_and_keeps_the_record(
    client: APIClient, account: tuple[User, str], note: dict[str, Any]
) -> None:
    patch(client, account[1], note["id"], "edited elsewhere")

    refused = delete(client, account[1], note["id"], match='"1"')

    assert refused.status_code == 412
    assert Note.objects.count() == 1


def test_a_delete_from_the_current_version_or_with_no_header_succeeds(
    client: APIClient, account: tuple[User, str]
) -> None:
    first = create(client, account[1], key='"aaaaaaaaaaaaaaaaaaaa"').json()
    second = create(
        client, account[1], {"title": "two"}, key='"bbbbbbbbbbbbbbbbbbbb"'
    ).json()

    with_header = delete(client, account[1], first["id"], match='"1"')
    without = delete(client, account[1], second["id"])

    assert (with_header.status_code, without.status_code) == (204, 204)
    assert Note.objects.count() == 0


# --- another user's record --------------------------------------------------------------------


def test_another_users_record_is_hidden_from_every_method(
    client: APIClient, note: dict[str, Any]
) -> None:
    stranger = APIClient()
    _, body = start_session(stranger)
    stranger.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")

    assert_hidden_from_other_users(stranger, detail(note["id"]))


# --- a create under a key the client chose ----------------------------------------------------


def put_title(client: APIClient, token: str, title: str, **headers: str) -> Any:
    return client.put(
        f"{URL}by-title/{title}/", {}, headers={**bearer(token), **headers}
    )


def test_a_put_creates_the_record_and_answers_its_etag(
    client: APIClient, account: tuple[User, str]
) -> None:
    response = put_title(client, account[1], "2026-10")

    assert response.status_code == 201
    assert response["ETag"] == '"1"'


def test_if_none_match_star_refuses_to_replace_a_record_that_exists(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_title(client, account[1], "2026-10")

    refused = put_title(client, account[1], "2026-10", **{"If-None-Match": "*"})

    assert refused.status_code == 412
    assert first_error(refused)["code"] == "precondition_failed"
    assert Note.objects.get().version == 1


def test_if_none_match_star_lets_a_new_record_through(
    client: APIClient, account: tuple[User, str]
) -> None:
    created = put_title(client, account[1], "2026-10", **{"If-None-Match": "*"})

    assert created.status_code == 201


def test_without_if_none_match_a_put_replaces_the_record(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_title(client, account[1], "2026-10")

    replaced = put_title(client, account[1], "2026-10")

    assert replaced.status_code == 200
    assert replaced["ETag"] == '"2"'


def test_if_match_on_a_record_that_does_not_exist_yet_is_a_412(
    client: APIClient, account: tuple[User, str]
) -> None:
    refused = put_title(client, account[1], "2026-10", **{"If-Match": '"1"'})

    assert refused.status_code == 412
    assert Note.objects.count() == 0


def test_if_match_on_a_put_of_an_existing_record_compares_the_version(
    client: APIClient, account: tuple[User, str]
) -> None:
    put_title(client, account[1], "2026-10")

    stale = put_title(client, account[1], "2026-10", **{"If-Match": '"9"'})
    fresh = put_title(client, account[1], "2026-10", **{"If-Match": '"1"'})

    assert (stale.status_code, fresh.status_code) == (412, 200)
