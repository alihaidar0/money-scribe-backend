"""At most 10 live sessions, one per device, and the email to the owner about a new device (ADR 0009)."""

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from users import limits
from users.models import EndReason, SecurityEvent, Session, User
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    DEVICE_ID,
    bearer,
    device,
    first_error,
    new_device,
    refresh,
    sign_in,
    start_session,
)
from users.tests.session_helpers import SESSIONS, get, ids, sign_in_on
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db


def new_device_emails() -> list[Any]:
    return [
        m
        for m in mail.outbox
        if "new device" in m.subject.lower() or "جهاز جديد" in m.subject
    ]


def live(user: User) -> list[Session]:
    return list(Session.objects.filter(user=user, ended_at__isnull=True))


def fill(
    client: APIClient, user: User, count: int
) -> list[tuple[dict[str, Any], Session]]:
    """``count`` more live sessions on devices of their own, the first one the least recently active."""
    made = [sign_in_on(client, user) for _ in range(count)]
    now = timezone.now()
    for index, (_, session) in enumerate(made):
        Session.objects.filter(pk=session.pk).update(
            last_active_at=now - timedelta(hours=count - index)
        )
    return made


# --- the limit of 10 --------------------------------------------------------------------------


def test_the_eleventh_sign_in_ends_the_least_recently_active_session(
    api_client: APIClient,
) -> None:
    user = VerifiedUserFactory.create()
    made = fill(api_client, user, 10)
    oldest_body, oldest = made[0]

    _, newest = sign_in_on(api_client, user, device_name="New phone")

    assert len(live(user)) == 10
    oldest.refresh_from_db()
    assert (oldest.end_reason, oldest.ended_at is not None) == (
        EndReason.SESSION_LIMIT,
        True,
    )
    assert Session.objects.get(pk=newest.pk).ended_at is None
    assert {s.pk for s in live(user)} == {s.pk for _, s in made[1:]} | {newest.pk}
    # Only the least recently active one: the others are untouched.
    assert (
        Session.objects.filter(user=user, end_reason=EndReason.SESSION_LIMIT).count()
        == 1
    )
    assert oldest_body["access_token"]


def test_the_ended_device_is_told_session_limit_reached_on_its_next_call(
    api_client: APIClient,
) -> None:
    user = VerifiedUserFactory.create()
    made = fill(api_client, user, 10)
    first_body = made[0][0]

    sign_in_on(api_client, user)

    profile = api_client.get(
        f"{AUTH}/profile/", headers=bearer(first_body["access_token"])
    )
    assert (profile.status_code, first_error(profile)["code"]) == (
        401,
        "session_limit_reached",
    )
    renewed = refresh(api_client, first_body["refresh_token"])
    assert (renewed.status_code, first_error(renewed)["code"]) == (
        401,
        "session_limit_reached",
    )


def test_the_event_names_the_device_of_the_session_that_was_ended(
    api_client: APIClient,
) -> None:
    user = VerifiedUserFactory.create()
    made = fill(api_client, user, 10)
    _, oldest = made[0]

    sign_in_on(api_client, user)

    [event] = SecurityEvent.objects.filter(user=user, type="session_limit_reached")
    assert event.session_id == oldest.id
    assert (event.device_name, event.platform) == (oldest.device_name, oldest.platform)
    assert event.sign_in_method is None


def test_nine_live_sessions_and_a_tenth_sign_in_end_nothing(
    api_client: APIClient,
) -> None:
    user = VerifiedUserFactory.create()
    fill(api_client, user, 9)

    sign_in_on(api_client, user)

    assert len(live(user)) == 10
    assert not Session.objects.filter(
        user=user, end_reason=EndReason.SESSION_LIMIT
    ).exists()
    assert not SecurityEvent.objects.filter(type="session_limit_reached").exists()


def test_a_session_whose_refresh_token_ran_out_does_not_count_toward_the_limit(
    api_client: APIClient,
) -> None:
    user = VerifiedUserFactory.create()
    made = fill(api_client, user, 10)
    for _, session in made[:3]:
        Session.objects.filter(pk=session.pk).update(
            rotated_at=timezone.now() - timedelta(days=31)
        )

    sign_in_on(api_client, user)

    assert not Session.objects.filter(
        user=user, end_reason=EndReason.SESSION_LIMIT
    ).exists()


def test_an_ended_session_does_not_count_either(api_client: APIClient) -> None:
    user = VerifiedUserFactory.create()
    made = fill(api_client, user, 10)
    Session.objects.filter(pk=made[5][1].pk).update(
        ended_at=timezone.now(), end_reason="signed_out"
    )

    sign_in_on(api_client, user)

    assert not Session.objects.filter(
        user=user, end_reason=EndReason.SESSION_LIMIT
    ).exists()


def test_the_limit_is_per_account(api_client: APIClient) -> None:
    user = VerifiedUserFactory.create()
    stranger = VerifiedUserFactory.create()
    fill(api_client, user, 10)
    _, theirs = sign_in_on(api_client, stranger)

    sign_in_on(api_client, user)

    assert Session.objects.get(pk=theirs.pk).ended_at is None


def test_the_list_never_shows_more_than_ten(api_client: APIClient) -> None:
    user = VerifiedUserFactory.create()
    fill(api_client, user, 10)
    body, _ = sign_in_on(api_client, user)

    assert len(ids(get(api_client, SESSIONS, body["access_token"]))) == 10


# --- one live session per device --------------------------------------------------------------


def test_a_sign_in_from_the_same_device_replaces_the_older_session(
    api_client: APIClient,
) -> None:
    user, first = start_session(api_client)
    old = Session.objects.get(user=user)

    second = sign_in(api_client, user.email).json()

    old.refresh_from_db()
    assert old.end_reason == EndReason.REPLACED
    assert len(live(user)) == 1
    profile = api_client.get(f"{AUTH}/profile/", headers=bearer(first["access_token"]))
    assert (profile.status_code, first_error(profile)["code"]) == (
        401,
        "session_signed_out",
    )
    renewed = refresh(api_client, first["refresh_token"])
    assert first_error(renewed)["code"] == "session_signed_out"
    assert (
        api_client.get(
            f"{AUTH}/profile/", headers=bearer(second["access_token"])
        ).status_code
        == 200
    )


def test_replacing_writes_no_event_but_the_sign_in(api_client: APIClient) -> None:
    user, _ = start_session(api_client)

    sign_in(api_client, user.email)

    types = list(SecurityEvent.objects.filter(user=user).values_list("type", flat=True))
    assert sorted(types) == ["signed_in", "signed_in"]


def test_only_the_same_device_is_replaced(api_client: APIClient) -> None:
    user, _ = start_session(api_client)
    _, other = sign_in_on(api_client, user)

    sign_in(api_client, user.email)

    assert Session.objects.get(pk=other.pk).ended_at is None
    assert len(live(user)) == 2


def test_an_ended_session_of_the_device_is_left_as_it_was(
    api_client: APIClient,
) -> None:
    user, first = start_session(api_client)
    mine = Session.objects.get(user=user)
    api_client.post(f"{AUTH}/sign-out/", {}, headers=bearer(first["access_token"]))

    sign_in(api_client, user.email)

    mine.refresh_from_db()
    assert mine.end_reason == EndReason.SIGNED_OUT


def test_replacing_makes_room_so_the_limit_is_not_triggered(
    api_client: APIClient,
) -> None:
    user, _ = start_session(api_client)
    fill(api_client, user, 9)  # ten live, one of them on the default device

    sign_in(api_client, user.email)  # the default device again

    assert len(live(user)) == 10
    assert not Session.objects.filter(
        user=user, end_reason=EndReason.SESSION_LIMIT
    ).exists()
    assert Session.objects.filter(user=user, end_reason=EndReason.REPLACED).count() == 1


def test_the_devices_show_once_among_the_active_sessions(api_client: APIClient) -> None:
    user, _ = start_session(api_client)
    body = {}
    for _ in range(3):
        body = sign_in(api_client, user.email).json()

    shown = get(api_client, SESSIONS, body["access_token"]).json()["results"]

    assert [s["device_id"] for s in shown] == [DEVICE_ID]


# --- the email about a new device -------------------------------------------------------------


def test_the_first_sign_in_of_an_account_is_not_announced(
    api_client: APIClient,
) -> None:
    start_session(api_client)

    assert new_device_emails() == []


def test_a_sign_in_from_a_device_the_account_has_not_used_tells_the_owner(
    api_client: APIClient,
) -> None:
    user, _ = start_session(api_client)

    sign_in_on(api_client, user, device_name="Galaxy S24")

    [message] = new_device_emails()
    assert message.to == [user.email]
    assert "Galaxy S24" in message.body
    assert "127.0.0.0/24" in message.body  # the network, never the address
    assert "127.0.0.1" not in message.body
    assert "UTC" in message.body  # the time, in the person's zone
    assert "change your password" in message.body
    assert "http" not in message.body


def test_a_known_device_is_not_announced_again(api_client: APIClient) -> None:
    user, first = start_session(api_client)
    api_client.post(f"{AUTH}/sign-out/", {}, headers=bearer(first["access_token"]))

    sign_in(api_client, user.email)  # the same device, after a sign-out

    assert new_device_emails() == []


def test_the_email_follows_the_language_and_the_zone_of_the_account(
    api_client: APIClient,
) -> None:
    user, _ = start_session(api_client, language="ar", time_zone="Asia/Riyadh")

    sign_in_on(api_client, user)

    [message] = new_device_emails()
    assert "جهاز جديد" in message.subject
    assert "Asia/Riyadh" in message.body


def test_a_device_whose_sessions_were_purged_is_new_again(
    api_client: APIClient,
) -> None:
    user, first = start_session(api_client)
    other_body, other = sign_in_on(api_client, user)
    Session.objects.filter(pk=other.pk).delete()
    assert other_body["access_token"]
    mail.outbox.clear()

    sign_in(api_client, user.email, device=device(device_id=str(other.device_id)))

    assert len(new_device_emails()) == 1
    assert first["access_token"]


def test_the_email_goes_out_at_most_five_an_hour_per_account(
    api_client: APIClient,
) -> None:
    user, _ = start_session(api_client)

    for _ in range(7):
        sign_in_on(api_client, user)

    assert len(new_device_emails()) == 5


def test_the_cap_is_per_account(api_client: APIClient) -> None:
    user, _ = start_session(api_client)
    stranger, _ = start_session(api_client)
    for _ in range(6):
        sign_in_on(api_client, user)

    sign_in_on(api_client, stranger)

    assert [m.to for m in new_device_emails()].count([stranger.email]) == 1


def test_a_down_limit_store_sends_no_email_instead_of_a_flood(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(limits, "cache", BrokenCache())

    assert limits.allow_new_device_email(VerifiedUserFactory.create().id) is False


def test_a_failing_mail_server_never_fails_the_sign_in(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _ = start_session(api_client)

    def broken(*args: Any, **kwargs: Any) -> int:
        raise OSError("the mail server is down")

    monkeypatch.setattr("users.emails.send_mail", broken)

    response = sign_in(api_client, user.email, device=new_device())

    assert response.status_code == 200
