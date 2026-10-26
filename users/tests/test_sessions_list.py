"""The list of the account's sessions: the live ones and the ended ones (ADR 0009)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIClient

from conftest import Clock
from users import services
from users.models import EndReason, Session, SignInMethod, User
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    DEVICE_ID,
    bearer,
    device,
    first_error,
    new_device,
    sign_in,
    start_session,
)
from users.tests.session_helpers import SESSIONS, get, ids, sign_in_on, walk

pytestmark = pytest.mark.django_db

KEYS = {
    "id",
    "device_id",
    "device_name",
    "platform",
    "os_version",
    "app_version",
    "signed_in_at",
    "last_active_at",
    "expires_at",
    "is_current",
    "sign_in_method",
    "network",
    "ended_at",
    "end_reason",
}


def make(user: User, **fields: Any) -> Session:
    """A session straight in the database, on a device of its own."""
    now = timezone.now()
    values: dict[str, Any] = {
        "user": user,
        "device_id": new_device()["device_id"],
        "device_name": "Old phone",
        "platform": "android",
        "app_version": "1.0.0+1",
        "created_at": now,
        "last_active_at": now,
        "rotated_at": now,
        "expires_at": now + timedelta(days=90),
        "refresh_hash": "x" * 64,
    }
    return Session.objects.create(**{**values, **fields})


# --- the active list --------------------------------------------------------------------------


def test_the_active_list_is_the_whole_list_with_the_current_session_and_its_fields(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    session = Session.objects.get(user=user)

    response = get(api_client, SESSIONS, body["access_token"])

    assert response.status_code == 200
    assert response.json()["next"] is None and response.json()["previous"] is None
    [item] = response.json()["results"]
    assert set(item) == KEYS
    assert item == {
        "id": str(session.id),
        "device_id": DEVICE_ID,
        "device_name": "Pixel 8",
        "platform": "android",
        "os_version": "Android 15",
        "app_version": "1.0.0+1",
        "signed_in_at": "2026-10-15T12:00:00Z",
        "last_active_at": "2026-10-15T12:00:00Z",
        "expires_at": "2027-01-13T12:00:00Z",
        "is_current": True,
        "sign_in_method": "password",
        "network": "127.0.0.0/24",
        "ended_at": None,
        "end_reason": None,
    }


def test_status_active_is_the_default_and_names_the_same_list(
    api_client: APIClient,
) -> None:
    _, body = start_session(api_client)

    assert (
        get(api_client, SESSIONS, body["access_token"]).json()
        == get(api_client, SESSIONS, body["access_token"], status="active").json()
    )


def test_the_current_session_is_first_then_the_most_recently_active(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    mine = Session.objects.get(user=user)
    now = timezone.now()
    older = make(user, last_active_at=now - timedelta(hours=5))
    newest = make(user, last_active_at=now - timedelta(minutes=1))
    middle = make(user, last_active_at=now - timedelta(hours=1))
    Session.objects.filter(pk=mine.pk).update(last_active_at=now - timedelta(days=3))

    response = get(api_client, SESSIONS, body["access_token"])

    assert ids(response) == [str(s.id) for s in (mine, newest, middle, older)]
    assert [i["is_current"] for i in response.json()["results"]] == [
        True,
        False,
        False,
        False,
    ]


def test_sessions_active_at_the_same_moment_are_ordered_by_id_descending(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    mine = Session.objects.get(user=user)
    same = timezone.now() - timedelta(hours=1)
    others = [make(user, last_active_at=same) for _ in range(3)]

    response = get(api_client, SESSIONS, body["access_token"])

    assert ids(response) == [str(mine.id)] + [
        str(s.id) for s in sorted(others, key=lambda s: s.id, reverse=True)
    ]


def test_limit_and_cursor_are_accepted_and_ignored_for_the_active_list(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    make(user)

    for params in ({"limit": 1}, {"limit": 0}, {"limit": "x"}, {"cursor": "garbage"}):
        response = get(api_client, SESSIONS, body["access_token"], **params)

        assert response.status_code == 200
        assert len(response.json()["results"]) == 2
        assert response.json()["next"] is None


def test_an_ended_session_is_not_active(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    gone = make(user, ended_at=timezone.now(), end_reason=EndReason.SIGNED_OUT)

    assert str(gone.id) not in ids(get(api_client, SESSIONS, body["access_token"]))


def test_a_session_whose_refresh_token_ran_out_is_not_active(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    lapsed = make(user, rotated_at=timezone.now() - timedelta(days=31))
    still = make(user, rotated_at=timezone.now() - timedelta(days=29))

    listed = ids(get(api_client, SESSIONS, body["access_token"]))

    assert str(lapsed.id) not in listed
    assert str(still.id) in listed


def test_a_session_past_its_90_day_limit_is_not_active(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    past = make(user, expires_at=timezone.now() - timedelta(seconds=1))

    assert str(past.id) not in ids(get(api_client, SESSIONS, body["access_token"]))


def test_the_list_is_the_persons_own(api_client: APIClient) -> None:
    _, body = start_session(api_client)
    stranger = VerifiedUserFactory.create()
    theirs = make(stranger)

    listed = ids(get(api_client, SESSIONS, body["access_token"]))
    ended = ids(get(api_client, SESSIONS, body["access_token"], status="ended"))

    assert str(theirs.id) not in listed + ended
    assert len(listed) == 1


def test_every_sign_in_method_is_shown_as_it_was(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    for method in (SignInMethod.EMAIL_CODE, SignInMethod.DEVICE_KEY):
        make(user, sign_in_method=method)

    shown = {
        item["sign_in_method"]
        for item in get(api_client, SESSIONS, body["access_token"]).json()["results"]
    }

    assert shown == {"password", "email_code", "device_key"}


@pytest.mark.parametrize("method", list(SignInMethod))
def test_a_session_stores_how_it_was_started(method: SignInMethod) -> None:
    user = VerifiedUserFactory.create()
    now = timezone.now()

    with transaction.atomic():
        signed = services.start_session(
            user,
            services.DeviceInfo(
                device_id=uuid.UUID(new_device()["device_id"]),
                device_name="Phone",
                platform="android",
                app_version="1.0",
            ),
            services.ClientInfo(ip="203.0.113.77"),
            now,
            method=method,
        )

    assert signed.session.sign_in_method == method
    assert Session.objects.get(pk=signed.session.pk).sign_in_method == method


def test_the_network_is_the_address_cut_to_its_network(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    make(user, ip="203.0.113.0")
    make(user, ip="2001:db8:1::")
    make(user, ip=None)

    networks = {
        item["network"]
        for item in get(api_client, SESSIONS, body["access_token"]).json()["results"]
    }

    assert networks == {"127.0.0.0/24", "203.0.113.0/24", "2001:db8:1::/48", None}


def test_no_full_address_and_no_user_agent_is_returned(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    make(user, ip="198.51.100.0", user_agent="Mozilla/5.0 secret-agent")

    text = get(api_client, SESSIONS, body["access_token"]).content.decode()

    assert "secret-agent" not in text and "user_agent" not in text
    assert '"ip"' not in text and "refresh" not in text


# --- the ended list ---------------------------------------------------------------------------


def test_the_ended_list_shows_the_reason_and_is_never_current(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    gone = make(
        user, ended_at=now - timedelta(days=2), end_reason=EndReason.PASSWORD_CHANGED
    )

    response = get(api_client, SESSIONS, body["access_token"], status="ended")

    [item] = response.json()["results"]
    assert item["id"] == str(gone.id)
    assert (item["ended_at"], item["end_reason"], item["is_current"]) == (
        "2026-10-13T12:00:00Z",
        "password_changed",
        False,
    )
    assert set(item) == KEYS


@pytest.mark.parametrize(
    "reason", [r for r in EndReason if r != EndReason.ACCOUNT_DELETED]
)
def test_every_end_reason_is_shown_as_stored(
    api_client: APIClient, reason: EndReason
) -> None:
    user, body = start_session(api_client)
    make(user, ended_at=timezone.now() - timedelta(hours=1), end_reason=reason)

    [item] = get(api_client, SESSIONS, body["access_token"], status="ended").json()[
        "results"
    ]

    assert item["end_reason"] == reason.value


def test_an_ended_session_is_shown_for_90_days_after_it_ended_and_not_longer(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    fresh = make(
        user, ended_at=now - timedelta(days=89, hours=23), end_reason="signed_out"
    )
    stale = make(
        user, ended_at=now - timedelta(days=90, hours=1), end_reason="signed_out"
    )

    listed = ids(get(api_client, SESSIONS, body["access_token"], status="ended"))

    assert str(fresh.id) in listed
    assert str(stale.id) not in listed


def test_a_session_that_ran_out_is_ended_with_the_moment_it_ran_out(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    rotated = now - timedelta(days=40)
    lapsed = make(user, created_at=now - timedelta(days=50), rotated_at=rotated)

    [item] = get(api_client, SESSIONS, body["access_token"], status="ended").json()[
        "results"
    ]

    expected = rotated + timedelta(days=30)
    assert item["id"] == str(lapsed.id)
    assert item["end_reason"] == "expired"
    assert item["ended_at"] == expected.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert item["is_current"] is False
    assert Session.objects.get(pk=lapsed.pk).ended_at is None  # nothing was written


def test_a_session_at_its_90_day_limit_expired_when_the_limit_came(
    api_client: APIClient, clock: Clock
) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    limit = now - timedelta(days=2)
    make(
        user,
        created_at=limit - timedelta(days=90),
        rotated_at=limit - timedelta(days=5),  # a token that would live 25 more days
        expires_at=limit,
    )

    [item] = get(api_client, SESSIONS, body["access_token"], status="ended").json()[
        "results"
    ]

    assert item["end_reason"] == "expired"
    assert item["ended_at"] == limit.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_session_that_ran_out_more_than_90_days_ago_is_not_shown(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    make(user, rotated_at=timezone.now() - timedelta(days=125))

    assert (
        get(api_client, SESSIONS, body["access_token"], status="ended").json()[
            "results"
        ]
        == []
    )


def test_the_ended_list_is_newest_sign_in_first_and_follows_next(
    api_client: APIClient,
) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    made = [
        make(
            user,
            created_at=now - timedelta(days=5 - n),
            ended_at=now - timedelta(days=1),
            end_reason="signed_out",
        )
        for n in range(5)
    ]
    expected = [str(s.id) for s in reversed(made)]

    pages = walk(api_client, body["access_token"], SESSIONS, status="ended", limit=2)

    assert [len(p) for p in pages] == [2, 2, 1]
    assert [i for page in pages for i in page] == expected


def test_replaced_sessions_of_one_device_share_its_device_id_in_the_ended_list(
    api_client: APIClient,
) -> None:
    user, _ = start_session(api_client)
    last = sign_in(api_client, user.email).json()
    for _ in range(3):
        last = sign_in(api_client, user.email).json()

    ended = get(api_client, SESSIONS, last["access_token"], status="ended").json()[
        "results"
    ]
    live = get(api_client, SESSIONS, last["access_token"]).json()["results"]

    assert len(ended) == 4 and len(live) == 1
    assert {i["device_id"] for i in ended + live} == {DEVICE_ID}
    assert {i["end_reason"] for i in ended} == {"replaced"}


def test_an_empty_ended_list_is_200(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    response = get(api_client, SESSIONS, body["access_token"], status="ended")

    assert response.status_code == 200
    assert response.json() == {"next": None, "previous": None, "results": []}


def test_a_session_ended_by_a_password_change_appears_with_its_reason(
    api_client: APIClient,
) -> None:
    from users.tests.helpers import AUTH

    user, first = start_session(api_client)
    second, _ = sign_in_on(api_client, user)
    api_client.post(
        f"{AUTH}/password/change/",
        {"current_password": "valid-pass-1", "password": "a-new-passphrase-4321"},
        headers=bearer(first["access_token"]),
    )

    pair = api_client.post(
        f"{AUTH}/sign-in/",
        {"email": user.email, "password": "a-new-passphrase-4321", "device": device()},
    ).json()
    ended = get(api_client, SESSIONS, pair["access_token"], status="ended").json()[
        "results"
    ]

    assert {i["end_reason"] for i in ended} == {"password_changed", "replaced"}
    assert second["access_token"]


# --- the parameters and who may ask -----------------------------------------------------------


def test_a_status_that_is_not_one_of_the_two_is_invalid_choice(
    api_client: APIClient,
) -> None:
    _, body = start_session(api_client)

    for value in ("all", "Active", "ended ", "1"):
        response = get(api_client, SESSIONS, body["access_token"], status=value)

        assert response.status_code == 400
        error = first_error(response)
        assert (error["code"], error["attr"]) == ("invalid_choice", "status")


@pytest.mark.parametrize("limit", ["0", "-1", "x", "1.5"])
def test_a_bad_limit_of_the_ended_list_is_invalid_on_limit(
    api_client: APIClient, limit: str
) -> None:
    _, body = start_session(api_client)

    response = get(
        api_client, SESSIONS, body["access_token"], status="ended", limit=limit
    )

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("invalid", "limit")


def test_the_largest_page_of_the_ended_list_is_100(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    now = timezone.now()
    for _ in range(105):
        make(user, ended_at=now - timedelta(hours=1), end_reason="signed_out")

    response = get(
        api_client, SESSIONS, body["access_token"], status="ended", limit=500
    )

    assert len(response.json()["results"]) == 100


def test_the_lists_need_a_signed_in_person(api_client: APIClient) -> None:
    assert api_client.get(SESSIONS).status_code == 401
    assert api_client.get(f"{SESSIONS}?status=ended").status_code == 401


def test_only_get_is_offered_on_the_collection(api_client: APIClient) -> None:
    _, body = start_session(api_client)

    assert (
        api_client.post(SESSIONS, {}, headers=bearer(body["access_token"])).status_code
        == 405
    )
    assert (
        api_client.delete(SESSIONS, headers=bearer(body["access_token"])).status_code
        == 405
    )


def test_the_lists_count_as_reads_not_as_actions(
    api_client: APIClient, settings: Any
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "reads": "3/min",
            "session_actions": "1/hour",
        },
    }
    _, body = start_session(api_client)

    codes = [
        get(api_client, SESSIONS, body["access_token"], status=s).status_code
        for s in ("active", "ended", "active", "ended")
    ]

    assert codes == [200, 200, 200, 429]
