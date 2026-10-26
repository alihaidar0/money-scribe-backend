"""The security activity read: newest first, kept 90 days, nothing but what ADR 0022 allows."""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from conftest import Clock
from users import events
from users.models import SecurityEvent, SecurityEventType, Session
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import AUTH, PASSWORD, bearer, first_error, start_session
from users.tests.session_helpers import HISTORY, get, ids, sign_in_on, walk

pytestmark = pytest.mark.django_db

KEYS = {
    "id",
    "type",
    "occurred_at",
    "device_name",
    "platform",
    "app_version",
    "sign_in_method",
    "session_id",
    "kinds",
    "count",
}


def write(
    user: Any, kind: SecurityEventType, when: Any, **fields: Any
) -> SecurityEvent:
    return SecurityEvent.objects.create(
        user=user, type=kind, occurred_at=when, **fields
    )


def test_a_sign_in_is_an_entry_with_the_device_and_how_the_person_signed_in(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    session = Session.objects.get(user=user)

    response = get(api_client, HISTORY, body["access_token"])

    assert response.status_code == 200
    [entry] = response.json()["results"]
    assert set(entry) == KEYS
    assert entry == {
        "id": entry["id"],
        "type": "signed_in",
        "occurred_at": "2026-10-15T12:00:00Z",
        "device_name": "Pixel 8",
        "platform": "android",
        "app_version": "1.0.0+1",
        "sign_in_method": "password",
        "session_id": str(session.id),
        "kinds": None,
        "count": None,
    }
    assert response.json()["next"] is None and response.json()["previous"] is None


def test_the_entries_are_newest_first_and_the_larger_id_breaks_a_tie(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    SecurityEvent.objects.all().delete()
    old = write(user, SecurityEventType.PASSWORD_CHANGED, now - timedelta(days=3))
    same = [
        write(user, SecurityEventType.SIGNED_OUT, now - timedelta(days=1))
        for _ in range(3)
    ]
    new = write(user, SecurityEventType.EMAIL_CHANGED, now)

    listed = ids(get(api_client, HISTORY, body["access_token"]))

    assert listed == [
        str(new.id),
        *[str(e.id) for e in sorted(same, key=lambda e: e.id, reverse=True)],
        str(old.id),
    ]


def test_entries_follow_next_without_a_gap_or_a_repeat(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    SecurityEvent.objects.all().delete()
    made = [
        write(user, SecurityEventType.SIGNED_OUT, now - timedelta(hours=n))
        for n in range(5)
    ]

    pages = walk(api_client, body["access_token"], HISTORY, limit=2)

    assert [len(p) for p in pages] == [2, 2, 1]
    assert [i for page in pages for i in page] == [str(e.id) for e in made]


def test_the_default_page_is_50_and_the_largest_100(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    SecurityEvent.objects.bulk_create(
        [
            SecurityEvent(
                user=user, type="signed_out", occurred_at=now - timedelta(minutes=n)
            )
            for n in range(120)
        ]
    )

    assert len(get(api_client, HISTORY, body["access_token"]).json()["results"]) == 50
    assert (
        len(get(api_client, HISTORY, body["access_token"], limit=100).json()["results"])
        == 100
    )
    assert (
        len(get(api_client, HISTORY, body["access_token"], limit=500).json()["results"])
        == 100
    )


def test_a_bad_limit_is_invalid_on_limit(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = get(api_client, HISTORY, body["access_token"], limit="0")

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("invalid", "limit")


def test_an_entry_older_than_90_days_is_never_returned_even_before_the_purge(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    kept = write(user, SecurityEventType.SIGNED_OUT, now - timedelta(days=89, hours=23))
    write(user, SecurityEventType.SIGNED_OUT, now - timedelta(days=90, hours=1))

    listed = ids(get(api_client, HISTORY, body["access_token"]))

    assert str(kept.id) in listed
    assert len(listed) == 2  # the entry kept and the sign-in of this very session


def test_an_empty_history_is_200_with_no_results(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    SecurityEvent.objects.all().delete()

    response = get(api_client, HISTORY, body["access_token"])

    assert response.json() == {"next": None, "previous": None, "results": []}


def test_the_history_is_the_persons_own(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    stranger = VerifiedUserFactory.create()
    theirs = write(stranger, SecurityEventType.PASSWORD_CHANGED, timezone.now())

    assert str(theirs.id) not in ids(get(api_client, HISTORY, body["access_token"]))


def test_an_export_and_an_import_show_their_kinds_and_row_count(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    session = Session.objects.get(user=user)
    events.record_data_event(
        user,
        SecurityEventType.DATA_EXPORTED,
        session,
        kinds=["transactions", "debts"],
        count=125,
    )

    entries = {
        e["type"]: e
        for e in get(api_client, HISTORY, body["access_token"]).json()["results"]
    }

    assert entries["data_exported"]["kinds"] == ["transactions", "debts"]
    assert entries["data_exported"]["count"] == 125
    assert entries["data_exported"]["session_id"] == str(session.id)
    assert (
        entries["signed_in"]["kinds"] is None and entries["signed_in"]["count"] is None
    )


def test_a_password_reset_has_no_device_and_no_session(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    events.record_account_event(user, SecurityEventType.PASSWORD_RESET)

    entry = next(
        e
        for e in get(api_client, HISTORY, body["access_token"]).json()["results"]
        if e["type"] == "password_reset"
    )

    assert (
        entry["device_name"],
        entry["platform"],
        entry["app_version"],
        entry["session_id"],
    ) == (
        None,
        None,
        None,
        None,
    )


def test_the_sign_in_method_is_on_a_sign_in_only(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    _, other = sign_in_on(api_client, user)
    api_client.delete(
        f"{AUTH}/sessions/{other.id}/", headers=bearer(body["access_token"])
    )

    entries = get(api_client, HISTORY, body["access_token"]).json()["results"]

    assert {e["type"] for e in entries} == {"signed_in", "session_revoked"}
    for entry in entries:
        assert (entry["sign_in_method"] is not None) == (entry["type"] == "signed_in")


def test_what_the_person_did_shows_up_in_the_order_it_happened(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    clock.advance(minutes=1)
    _, other = sign_in_on(api_client, user)
    clock.advance(minutes=1)
    api_client.delete(
        f"{AUTH}/sessions/{other.id}/", headers=bearer(body["access_token"])
    )
    clock.advance(minutes=1)
    api_client.post(
        f"{AUTH}/sessions/revoke-others/",
        {"current_password": PASSWORD},
        headers=bearer(body["access_token"]),
    )

    types = [
        e["type"]
        for e in get(api_client, HISTORY, body["access_token"]).json()["results"]
    ]

    # Newest first: the revoke, then the two sign-ins; revoke-others found nothing more to end.
    assert types == ["session_revoked", "signed_in", "signed_in"]


def test_no_ip_user_agent_or_name_is_in_an_entry(api_client: APIClient) -> None:
    user, body = start_session(api_client)

    text = get(api_client, HISTORY, body["access_token"]).content.decode()

    assert "127.0.0.1" not in text and "user_agent" not in text and '"ip"' not in text
    assert user.email not in text and user.name not in text


def test_the_history_needs_a_signed_in_person_and_only_get_is_offered(
    api_client: APIClient,
) -> None:
    _, body = start_session(api_client)

    assert api_client.get(HISTORY).status_code == 401
    assert (
        api_client.post(HISTORY, {}, headers=bearer(body["access_token"])).status_code
        == 405
    )
    assert (
        api_client.delete(HISTORY, headers=bearer(body["access_token"])).status_code
        == 405
    )
