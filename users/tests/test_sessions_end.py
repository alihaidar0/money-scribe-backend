"""Ending sessions: one of them, or all the others (ADR 0009, ADR 0018, ADR 0022)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from users import limits
from users.models import (
    DeviceKey,
    EndReason,
    SecurityEvent,
    Session,
    User,
)
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    PASSWORD,
    bearer,
    first_error,
    refresh,
    start_session,
)
from users.tests.session_helpers import (
    REVOKE_OTHERS,
    SESSIONS,
    get,
    session_url,
    sign_in_on,
)
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db

SOME_ID = "0198f2d1-3b4c-7d5e-8f60-9a0b1c2d3e4f"


def key_of(user: User, device_id: Any, name: str = "Some phone") -> DeviceKey:
    return DeviceKey.objects.create(
        user=user,
        device_id=device_id,
        public_key=b"k" + uuid.uuid4().bytes,
        platform="android",
        device_name=name,
        expires_at=timezone.now() + timedelta(days=30),
    )


def end(client: APIClient, token: str, session_id: Any) -> Any:
    return client.delete(session_url(session_id), headers=bearer(token))


def revoke_others(client: APIClient, token: str, **body: Any) -> Any:
    payload = {"current_password": PASSWORD, **body}
    return client.post(REVOKE_OTHERS, payload, headers=bearer(token))


def events(user: User, event_type: str) -> list[SecurityEvent]:
    return list(
        SecurityEvent.objects.filter(user=user, type=event_type).order_by(
            "occurred_at", "id"
        )
    )


# --- end one ----------------------------------------------------------------------------------


def test_another_session_ends_and_its_device_is_told_why(api_client: APIClient) -> None:
    user, mine = start_session(api_client)
    theirs, other = sign_in_on(api_client, user)

    response = end(api_client, mine["access_token"], other.id)

    assert response.status_code == 204
    assert response.content == b""
    other.refresh_from_db()
    assert (other.end_reason, other.ended_at is not None) == (
        EndReason.REVOKED_BY_USER,
        True,
    )
    profile = api_client.get(f"{AUTH}/profile/", headers=bearer(theirs["access_token"]))
    assert (profile.status_code, first_error(profile)["code"]) == (
        401,
        "session_revoked_by_user",
    )
    renewed = refresh(api_client, theirs["refresh_token"])
    assert first_error(renewed)["code"] == "session_revoked_by_user"
    assert (
        api_client.get(
            f"{AUTH}/profile/", headers=bearer(mine["access_token"])
        ).status_code
        == 200
    )


def test_ending_another_session_records_session_revoked_with_the_device_that_ended(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    _, other = sign_in_on(api_client, user, device_name="Old tablet")

    end(api_client, mine["access_token"], other.id)

    [event] = events(user, "session_revoked")
    assert event.session_id == other.id
    assert (event.device_name, event.platform) == ("Old tablet", "android")
    assert event.sign_in_method is None and event.kinds is None


def test_the_fingerprint_key_of_the_ended_device_goes_with_it(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    me = Session.objects.get(user=user)
    _, other = sign_in_on(api_client, user)
    gone = key_of(user, other.device_id, "Old tablet")
    unrelated = key_of(user, uuid.uuid4(), "Signed-out phone")
    current = key_of(user, me.device_id, "This phone")

    response = end(api_client, mine["access_token"], other.id)

    assert response.status_code == 204
    assert not DeviceKey.objects.filter(pk=gone.pk).exists()
    assert DeviceKey.objects.filter(pk__in=[unrelated.pk, current.pk]).count() == 2
    [event] = events(user, "device_key_removed")
    assert event.device_name == "Old tablet"
    assert event.session_id == me.id  # the session that asked


def test_the_key_of_the_ended_device_is_kept_when_it_is_the_current_device(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    me = Session.objects.get(user=user)
    # An older, still live session on the current device cannot exist (a sign-in replaces it), but a stale
    # row can: ending it must not remove the key the current device is using.
    stale = Session.objects.create(
        user=user,
        device_id=me.device_id,
        device_name="Pixel 8",
        platform="android",
        app_version="1.0",
        expires_at=timezone.now() + timedelta(days=90),
        refresh_hash="x" * 64,
    )
    key = key_of(user, me.device_id)

    end(api_client, mine["access_token"], stale.id)

    assert DeviceKey.objects.filter(pk=key.pk).exists()
    assert events(user, "device_key_removed") == []


def test_ending_the_current_session_is_a_sign_out_and_the_key_stays(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    me = Session.objects.get(user=user)
    key = key_of(user, me.device_id)

    response = end(api_client, mine["access_token"], me.id)

    assert response.status_code == 204
    me.refresh_from_db()
    assert me.end_reason == EndReason.SIGNED_OUT
    assert DeviceKey.objects.filter(pk=key.pk).exists()
    assert len(events(user, "signed_out")) == 1
    assert events(user, "session_revoked") == []
    profile = api_client.get(f"{AUTH}/profile/", headers=bearer(mine["access_token"]))
    assert first_error(profile)["code"] == "session_signed_out"


def test_ending_a_session_that_is_already_over_is_204_and_changes_nothing(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    _, other = sign_in_on(api_client, user)
    end(api_client, mine["access_token"], other.id)
    other.refresh_from_db()
    first_end = other.ended_at

    again = end(api_client, mine["access_token"], other.id)

    assert again.status_code == 204
    other.refresh_from_db()
    assert (other.ended_at, other.end_reason) == (first_end, EndReason.REVOKED_BY_USER)
    assert len(events(user, "session_revoked")) == 1


def test_a_session_whose_refresh_token_ran_out_is_over_too_and_stays_as_it_is(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    _, other = sign_in_on(api_client, user)
    Session.objects.filter(pk=other.pk).update(
        rotated_at=timezone.now() - timedelta(days=31)
    )
    key = key_of(user, other.device_id)

    response = end(api_client, mine["access_token"], other.id)

    assert response.status_code == 204
    other.refresh_from_db()
    assert (other.ended_at, other.end_reason) == (None, "")
    assert DeviceKey.objects.filter(pk=key.pk).exists()
    assert events(user, "session_revoked") == []


def test_another_persons_session_and_an_unknown_id_are_the_same_404(
    api_client: APIClient,
) -> None:
    _, mine = start_session(api_client)
    stranger = VerifiedUserFactory.create()
    _, theirs = sign_in_on(api_client, stranger)

    foreign = end(api_client, mine["access_token"], theirs.id)
    unknown = end(api_client, mine["access_token"], SOME_ID)

    assert (foreign.status_code, unknown.status_code) == (404, 404)
    assert foreign.json() == unknown.json()
    assert first_error(foreign)["code"] == "not_found"
    theirs.refresh_from_db()
    assert theirs.ended_at is None
    assert events(stranger, "session_revoked") == []


def test_another_persons_ended_session_is_404_too(api_client: APIClient) -> None:
    _, mine = start_session(api_client)
    stranger = VerifiedUserFactory.create()
    _, theirs = sign_in_on(api_client, stranger)
    Session.objects.filter(pk=theirs.pk).update(
        ended_at=timezone.now(), end_reason="signed_out"
    )

    assert end(api_client, mine["access_token"], theirs.id).status_code == 404


def test_the_ended_session_leaves_the_active_list_and_joins_the_ended_one(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    _, other = sign_in_on(api_client, user)

    end(api_client, mine["access_token"], other.id)

    active = get(api_client, SESSIONS, mine["access_token"]).json()["results"]
    ended = get(api_client, SESSIONS, mine["access_token"], status="ended").json()[
        "results"
    ]
    assert [i["id"] for i in active] == [
        str(Session.objects.get(user=user, ended_at__isnull=True).id)
    ]
    assert [(i["id"], i["end_reason"]) for i in ended] == [
        (str(other.id), "revoked_by_user")
    ]


def test_ending_needs_a_signed_in_person_and_only_delete_is_offered(
    api_client: APIClient,
) -> None:
    _, mine = start_session(api_client)

    assert api_client.delete(session_url(SOME_ID)).status_code == 401
    for method in ("get", "put", "patch", "post"):
        response = getattr(api_client, method)(
            session_url(SOME_ID), headers=bearer(mine["access_token"])
        )
        assert response.status_code == 405


def test_ending_counts_in_the_session_actions_group_and_keeps_working_when_the_store_is_down(
    api_client: APIClient, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "session_actions": "2/hour",
        },
    }
    user, mine = start_session(api_client)
    others = [sign_in_on(api_client, user)[1] for _ in range(3)]

    codes = [end(api_client, mine["access_token"], s.id).status_code for s in others]
    refused = end(api_client, mine["access_token"], others[0].id)

    assert codes == [204, 204, 429]
    assert (
        first_error(refused)["code"] == "throttled" and int(refused["Retry-After"]) >= 1
    )

    monkeypatch.setattr(limits, "cache", BrokenCache())
    assert end(api_client, mine["access_token"], others[2].id).status_code == 204


# --- end all the others -----------------------------------------------------------------------


def test_every_other_session_ends_and_every_key_but_the_current_devices_goes(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    me = Session.objects.get(user=user)
    first_body, first = sign_in_on(api_client, user)
    _, second = sign_in_on(api_client, user)
    kept = key_of(user, me.device_id, "This phone")
    key_of(user, first.device_id, "Tablet")
    key_of(user, uuid.uuid4(), "A signed-out phone")

    response = revoke_others(api_client, mine["access_token"])

    assert response.status_code == 200
    assert response.json() == {"ended": 2, "device_keys_removed": 2}
    assert list(DeviceKey.objects.filter(user=user)) == [kept]
    assert Session.objects.filter(user=user, ended_at__isnull=True).get().id == me.id
    for pair in (first_body,):
        profile = api_client.get(
            f"{AUTH}/profile/", headers=bearer(pair["access_token"])
        )
        assert first_error(profile)["code"] == "session_revoked_by_user"
    assert {
        s.end_reason for s in Session.objects.filter(pk__in=[first.pk, second.pk])
    } == {EndReason.REVOKED_BY_USER}
    assert (
        api_client.get(
            f"{AUTH}/profile/", headers=bearer(mine["access_token"])
        ).status_code
        == 200
    )


def test_it_records_one_event_for_each_session_and_each_key(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    _, first = sign_in_on(api_client, user, device_name="Tablet")
    _, second = sign_in_on(api_client, user, device_name="Laptop")
    key_of(user, first.device_id, "Tablet")
    key_of(user, uuid.uuid4(), "Old phone")

    revoke_others(api_client, mine["access_token"])

    revoked = events(user, "session_revoked")
    assert {e.session_id for e in revoked} == {first.id, second.id}
    assert {e.device_name for e in revoked} == {"Tablet", "Laptop"}
    assert {e.device_name for e in events(user, "device_key_removed")} == {
        "Tablet",
        "Old phone",
    }


def test_with_no_other_session_it_ends_none_and_still_removes_the_other_keys(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    key_of(user, uuid.uuid4(), "Signed-out phone")

    response = revoke_others(api_client, mine["access_token"])

    assert response.status_code == 200
    assert response.json() == {"ended": 0, "device_keys_removed": 1}


def test_a_session_that_ran_out_is_not_counted_as_ended_now(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    _, lapsed = sign_in_on(api_client, user)
    Session.objects.filter(pk=lapsed.pk).update(
        rotated_at=timezone.now() - timedelta(days=31)
    )

    assert revoke_others(api_client, mine["access_token"]).json()["ended"] == 0


def test_it_never_touches_another_persons_sessions_or_keys(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    stranger = VerifiedUserFactory.create()
    _, theirs = sign_in_on(api_client, stranger)
    their_key = key_of(stranger, theirs.device_id)

    revoke_others(api_client, mine["access_token"])

    theirs.refresh_from_db()
    assert theirs.ended_at is None
    assert DeviceKey.objects.filter(pk=their_key.pk).exists()
    assert user.sessions.filter(ended_at__isnull=True).count() == 1


def test_a_wrong_password_is_invalid_password_and_ends_nothing(
    api_client: APIClient,
) -> None:
    user, mine = start_session(api_client)
    _, other = sign_in_on(api_client, user)
    key = key_of(user, uuid.uuid4())

    response = revoke_others(
        api_client, mine["access_token"], current_password="not-my-password"
    )

    assert response.status_code == 400
    error = first_error(response)
    assert (error["code"], error["attr"]) == ("invalid_password", "current_password")
    other.refresh_from_db()
    assert other.ended_at is None
    assert DeviceKey.objects.filter(pk=key.pk).exists()


def test_a_missing_password_is_required_and_another_field_is_read_only(
    api_client: APIClient,
) -> None:
    _, mine = start_session(api_client)

    missing = api_client.post(REVOKE_OTHERS, {}, headers=bearer(mine["access_token"]))
    extra = revoke_others(api_client, mine["access_token"], keep="all")

    error = first_error(missing)
    assert (missing.status_code, error["code"], error["attr"]) == (
        400,
        "required",
        "current_password",
    )
    error = first_error(extra)
    assert (extra.status_code, error["code"], error["attr"]) == (
        400,
        "read_only",
        "keep",
    )


def test_five_wrong_passwords_lock_it_together_with_the_password_change(
    api_client: APIClient,
) -> None:
    _, mine = start_session(api_client)

    for _ in range(4):
        assert (
            revoke_others(
                api_client, mine["access_token"], current_password="nope"
            ).status_code
            == 400
        )
    fifth = revoke_others(api_client, mine["access_token"], current_password="nope")
    change = api_client.post(
        f"{AUTH}/password/change/",
        {"current_password": PASSWORD, "password": "a-new-passphrase-4321"},
        headers=bearer(mine["access_token"]),
    )

    assert fifth.status_code == 429
    assert first_error(fifth)["code"] == "password_attempts_exceeded"
    assert int(fifth["Retry-After"]) >= 1
    assert change.status_code == 429  # the lock is shared


def test_while_the_limit_store_is_down_it_is_503_and_changes_nothing(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, mine = start_session(api_client)
    _, other = sign_in_on(api_client, user)
    api_client.raise_request_exception = (
        False  # a client gets the 503, it does not see the exception
    )
    monkeypatch.setattr(limits, "cache", BrokenCache())

    response = revoke_others(api_client, mine["access_token"])

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"
    assert response["Retry-After"] == "30"
    other.refresh_from_db()
    assert other.ended_at is None


def test_it_shares_the_session_actions_group_with_ending_one(
    api_client: APIClient, settings: Any
) -> None:
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {
            **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
            "session_actions": "2/hour",
        },
    }
    user, mine = start_session(api_client)
    _, other = sign_in_on(api_client, user)

    first = revoke_others(api_client, mine["access_token"])
    second = end(api_client, mine["access_token"], other.id)
    third = revoke_others(api_client, mine["access_token"])

    assert (first.status_code, second.status_code, third.status_code) == (200, 204, 429)


def test_it_needs_a_signed_in_person_and_only_post_is_offered(
    api_client: APIClient,
) -> None:
    _, mine = start_session(api_client)

    assert (
        api_client.post(REVOKE_OTHERS, {"current_password": PASSWORD}).status_code
        == 401
    )
    assert (
        api_client.get(REVOKE_OTHERS, headers=bearer(mine["access_token"])).status_code
        == 405
    )
