"""The security events (ADR 0022): what is recorded, with which device, and what never is."""

import ast
import inspect
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from conftest import Clock
from users import device_keys, events, services
from users.models import (
    DATA_EVENT_TYPES,
    DeviceKey,
    SecurityEvent,
    SecurityEventType,
    Session,
    User,
)
from users.tasks import purge_old_security_events
from users.tests.device_key_helpers import KEYS, Phone, key_sign_in
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    DEVICE_ID,
    PASSWORD,
    bearer,
    device,
    emailed_code,
    refresh,
    sign_in,
    start_session,
)
from users.tests.test_device_keys import Account, enroll, open_account
from users.tests.test_password_reset import confirm_reset, get_reset_token

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "a-fine-passphrase-7"
OTHER_DEVICE = "6f1d2c3b-4a59-4e8f-8b7a-0c9d1e2f3a4b"


@pytest.fixture
def client(api_client: APIClient) -> APIClient:
    api_client.raise_request_exception = False
    return api_client


def trail(user: User) -> list[SecurityEvent]:
    """The user's events, oldest first."""
    return list(SecurityEvent.objects.filter(user=user).order_by("occurred_at", "id"))


def types(user: User) -> list[str]:
    return [event.type for event in trail(user)]


# --- what an event is -------------------------------------------------------------------------


def test_an_event_has_no_field_for_an_address_an_agent_or_a_value() -> None:
    names = {field.name for field in SecurityEvent._meta.get_fields()}

    assert names == {
        "id",
        "user",
        "type",
        "occurred_at",
        "session_id",
        "device_name",
        "platform",
        "app_version",
        "sign_in_method",
        "kinds",
        "count",
    }


def test_the_types_are_the_twelve_of_the_adr() -> None:
    assert {t.value for t in SecurityEventType} == {
        "signed_in",
        "signed_out",
        "session_revoked",
        "session_limit_reached",
        "password_changed",
        "password_reset",
        "email_changed",
        "device_key_added",
        "device_key_removed",
        "token_reuse_detected",
        "data_exported",
        "data_imported",
    }


def test_the_database_keeps_the_sign_in_method_on_a_sign_in_only() -> None:
    user = VerifiedUserFactory.create()

    with pytest.raises(IntegrityError), transaction.atomic():
        SecurityEvent.objects.create(
            user=user, type="signed_out", sign_in_method="password"
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        SecurityEvent.objects.create(user=user, type="signed_in")  # no method


def test_the_database_keeps_kinds_and_count_on_an_export_or_an_import_only() -> None:
    user = VerifiedUserFactory.create()

    with pytest.raises(IntegrityError), transaction.atomic():
        SecurityEvent.objects.create(
            user=user, type="signed_out", kinds=["debts"], count=3
        )
    for kind in DATA_EVENT_TYPES:
        SecurityEvent.objects.create(user=user, type=kind, kinds=["debts"], count=3)


def test_only_an_export_or_an_import_can_carry_kinds(api_client: APIClient) -> None:
    user, body = start_session(api_client)
    session = Session.objects.get()

    with pytest.raises(ValueError, match="Only an export or an import"):
        events.record_data_event(
            user, SecurityEventType.SIGNED_IN, session, kinds=["debts"], count=1
        )
    event = events.record_data_event(
        user, SecurityEventType.DATA_IMPORTED, session, kinds=["debts"], count=20
    )
    assert (event.kinds, event.count, event.session_id) == (
        ["debts"],
        20,
        session.id,
    )
    assert body["access_token"]


# --- signing in -------------------------------------------------------------------------------


def test_a_password_sign_in_is_recorded_with_its_device_and_method(
    client: APIClient,
) -> None:
    user, _ = start_session(client)

    (event,) = trail(user)
    session = Session.objects.get()
    assert event.type == "signed_in"
    assert event.sign_in_method == "password"
    assert event.session_id == session.id
    assert (event.device_name, event.platform, event.app_version) == (
        "Pixel 8",
        "android",
        "1.0.0+1",
    )


def test_a_failed_sign_in_records_nothing(client: APIClient) -> None:
    user = VerifiedUserFactory.create()

    wrong = sign_in(client, user.email, "not-the-password-0")

    assert wrong.status_code == 401
    assert SecurityEvent.objects.count() == 0


def test_the_verification_that_signs_a_new_account_in_is_recorded_as_an_email_code(
    client: APIClient,
) -> None:
    client.post(
        f"{AUTH}/register/",
        {"name": "Sara", "email": "sara@example.com", "password": NEW_PASSWORD},
    )
    response = client.post(
        f"{AUTH}/verify-email/",
        {"email": "sara@example.com", "code": emailed_code(), "device": device()},
    )

    assert response.status_code == 200
    (event,) = SecurityEvent.objects.all()
    assert (event.type, event.sign_in_method) == ("signed_in", "email_code")


def test_a_fingerprint_sign_in_is_recorded_as_a_device_key(
    client: APIClient,
) -> None:
    phone = Phone()
    account = open_account(client, device_id=DEVICE_ID)
    enroll(client, account, phone)
    SecurityEvent.objects.all().delete()

    response = key_sign_in(client, phone, DEVICE_ID)

    assert response.status_code == 200
    (event,) = SecurityEvent.objects.all()
    assert (event.type, event.sign_in_method) == ("signed_in", "device_key")
    assert event.user_id == account.user.id


def test_an_action_whose_event_cannot_be_written_does_not_happen(
    client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = VerifiedUserFactory.create()

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the event table is gone")

    monkeypatch.setattr(events, "_write", broken)

    response = sign_in(client, user.email)

    assert response.status_code == 500
    assert Session.objects.count() == 0


# --- signing out ------------------------------------------------------------------------------


def test_signing_out_with_the_access_token_is_recorded_once(
    client: APIClient,
) -> None:
    user, body = start_session(client)

    first = client.post(f"{AUTH}/sign-out/", headers=bearer(body["access_token"]))
    second = client.post(f"{AUTH}/sign-out/", headers=bearer(body["access_token"]))

    assert first.status_code == 204
    assert second.status_code == 401  # the session is over
    assert types(user) == ["signed_in", "signed_out"]
    out = trail(user)[1]
    assert out.session_id == Session.objects.get().id
    assert out.device_name == "Pixel 8"


def test_signing_out_with_the_refresh_token_is_recorded_once(
    client: APIClient,
) -> None:
    user, body = start_session(client)
    payload = {"refresh_token": body["refresh_token"]}

    first = client.post(f"{AUTH}/sign-out/", payload)
    again = client.post(f"{AUTH}/sign-out/", payload)

    assert (first.status_code, again.status_code) == (204, 204)
    assert types(user) == ["signed_in", "signed_out"]


# --- a refresh token used twice ---------------------------------------------------------------


def test_a_replayed_refresh_token_ends_the_session_and_is_recorded(
    client: APIClient, clock: Clock
) -> None:
    user, body = start_session(client)
    first = refresh(client, body["refresh_token"])
    assert first.status_code == 200
    clock.advance(seconds=31)  # past the replay window

    replay = refresh(client, body["refresh_token"])

    assert replay.status_code == 401
    assert replay.json()["errors"][0]["code"] == "session_token_reuse"
    assert types(user) == ["signed_in", "token_reuse_detected"]
    event = trail(user)[1]
    session = Session.objects.get()
    assert event.session_id == session.id  # the session that was ended
    assert event.device_name == session.device_name


def test_a_retry_inside_the_replay_window_records_nothing(
    client: APIClient, clock: Clock
) -> None:
    user, body = start_session(client)
    refresh(client, body["refresh_token"])

    retry = refresh(client, body["refresh_token"])

    assert retry.status_code == 200
    assert types(user) == ["signed_in"]


# --- the password and the email ---------------------------------------------------------------


def test_a_password_change_is_recorded_with_the_session_that_made_it(
    client: APIClient,
) -> None:
    user, body = start_session(client)

    response = client.post(
        f"{AUTH}/password/change/",
        {"current_password": PASSWORD, "password": NEW_PASSWORD},
        headers=bearer(body["access_token"]),
    )

    assert response.status_code == 200
    assert types(user) == ["signed_in", "password_changed"]
    event = trail(user)[1]
    assert event.session_id == Session.objects.get().id
    assert event.device_name == "Pixel 8"


def test_a_refused_password_change_records_nothing(client: APIClient) -> None:
    user, body = start_session(client)

    response = client.post(
        f"{AUTH}/password/change/",
        {"current_password": "wrong-password-0", "password": NEW_PASSWORD},
        headers=bearer(body["access_token"]),
    )

    assert response.status_code == 400
    assert types(user) == ["signed_in"]


def test_a_password_reset_is_recorded_without_a_device(client: APIClient) -> None:
    user = VerifiedUserFactory.create(email="sara@example.com")
    token = get_reset_token(client, "sara@example.com")

    response = confirm_reset(client, token)

    assert response.status_code == 204
    (event,) = trail(user)
    assert event.type == "password_reset"
    assert (event.session_id, event.device_name, event.platform) == (None, None, None)
    assert event.app_version is None


def test_a_confirmed_email_change_is_recorded_with_its_session(
    client: APIClient,
) -> None:
    user, body = start_session(client)
    client.post(
        f"{AUTH}/email-change/",
        {"email": "new.address@example.com", "current_password": PASSWORD},
        headers=bearer(body["access_token"]),
    )

    response = client.post(
        f"{AUTH}/email-change/confirm/",
        {"code": emailed_code()},
        headers=bearer(body["access_token"]),
    )

    assert response.status_code == 200
    assert types(user) == ["signed_in", "email_changed"]
    assert trail(user)[1].session_id == Session.objects.get().id


# --- device keys ------------------------------------------------------------------------------


def test_registering_a_key_is_recorded_with_the_device_of_the_key(
    client: APIClient,
) -> None:
    account = open_account(client, device_id=DEVICE_ID)

    enroll(client, account, Phone())

    assert types(account.user) == ["signed_in", "device_key_added"]
    event = trail(account.user)[1]
    key = DeviceKey.objects.get()
    assert event.session_id == Session.objects.get().id
    assert (event.device_name, event.platform) == (key.device_name, key.platform)
    assert event.app_version == "1.0.0+1"  # the acting session is on the key's device


def test_removing_the_key_of_this_device_is_recorded(client: APIClient) -> None:
    account = open_account(client, device_id=DEVICE_ID)
    enroll(client, account, Phone())

    response = client.delete(f"{KEYS}/current/", headers=bearer(account.access))

    assert response.status_code == 204
    assert types(account.user) == [
        "signed_in",
        "device_key_added",
        "device_key_removed",
    ]


def test_removing_a_key_of_another_device_names_that_device_and_the_acting_session(
    client: APIClient,
) -> None:
    phone = Phone()
    lost = open_account(client, device_id=OTHER_DEVICE)
    enroll(client, lost, phone)
    here = Account(
        lost.user,
        sign_in_with(client, lost.user, DEVICE_ID),
        DEVICE_ID,
        "ios",
    )

    response = client.delete(f"{KEYS}/{OTHER_DEVICE}/", headers=bearer(here.access))

    assert response.status_code == 204
    removed = [e for e in trail(lost.user) if e.type == "device_key_removed"]
    assert len(removed) == 1
    acting = Session.objects.get(device_id=DEVICE_ID)
    assert removed[0].session_id == acting.id
    assert removed[0].device_name == "Pixel 8"  # the lost phone, not the acting one
    assert removed[0].app_version is None  # the acting session is on another device


def sign_in_with(client: APIClient, user: User, device_id: str) -> str:
    response = sign_in(client, user.email, device=device(device_id=device_id))
    assert response.status_code == 200, response.json()
    return str(response.json()["access_token"])


def test_removing_a_key_that_is_not_there_records_nothing(client: APIClient) -> None:
    user, body = start_session(client)

    response = client.delete(f"{KEYS}/current/", headers=bearer(body["access_token"]))

    assert response.status_code == 204
    assert types(user) == ["signed_in"]


def test_a_password_change_records_the_removal_of_each_key(client: APIClient) -> None:
    phone = Phone()
    account = open_account(client, device_id=DEVICE_ID)
    enroll(client, account, phone)
    SecurityEvent.objects.all().delete()

    client.post(
        f"{AUTH}/password/change/",
        {"current_password": PASSWORD, "password": NEW_PASSWORD},
        headers=bearer(account.access),
    )

    assert sorted(types(account.user)) == ["device_key_removed", "password_changed"]
    assert not DeviceKey.objects.exists()


def test_a_password_reset_records_the_removal_of_each_key(client: APIClient) -> None:
    account = open_account(client, device_id=DEVICE_ID, email="sara@example.com")
    enroll(client, account, Phone())
    SecurityEvent.objects.all().delete()

    confirm_reset(client, get_reset_token(client, "sara@example.com"))

    assert sorted(types(account.user)) == ["device_key_removed", "password_reset"]
    removed = SecurityEvent.objects.get(type="device_key_removed")
    assert removed.session_id is None  # a reset has no acting session


def test_an_email_change_records_the_removal_of_each_key(client: APIClient) -> None:
    account = open_account(client, device_id=DEVICE_ID)
    enroll(client, account, Phone())
    SecurityEvent.objects.all().delete()
    client.post(
        f"{AUTH}/email-change/",
        {"email": "new.address@example.com", "current_password": PASSWORD},
        headers=bearer(account.access),
    )

    client.post(
        f"{AUTH}/email-change/confirm/",
        {"code": emailed_code()},
        headers=bearer(account.access),
    )

    assert sorted(types(account.user)) == ["device_key_removed", "email_changed"]


# --- the trail belongs to the account ---------------------------------------------------------


def test_deleting_the_account_deletes_its_trail(client: APIClient) -> None:
    user, body = start_session(client)
    other = VerifiedUserFactory.create()
    SecurityEvent.objects.create(user=other, type="signed_out")

    response = client.post(
        f"{AUTH}/account/delete/",
        {"password": PASSWORD},
        headers=bearer(body["access_token"]),
    )

    assert response.status_code == 204
    assert not User.objects.filter(pk=user.pk).exists()
    assert list(SecurityEvent.objects.values_list("user_id", flat=True)) == [other.id]


def test_one_user_never_gets_the_events_of_another(client: APIClient) -> None:
    first, _ = start_session(client)
    second, _ = start_session(client)

    assert {e.user_id for e in trail(first)} == {first.id}
    assert {e.user_id for e in trail(second)} == {second.id}


# --- the purge --------------------------------------------------------------------------------


def test_the_purge_deletes_events_older_than_90_days_and_keeps_the_rest() -> None:
    user = VerifiedUserFactory.create()
    now = timezone.now()
    old = SecurityEvent.objects.create(
        user=user, type="signed_out", occurred_at=now - timedelta(days=91)
    )
    edge = SecurityEvent.objects.create(
        user=user, type="signed_out", occurred_at=now - timedelta(days=89)
    )
    fresh = SecurityEvent.objects.create(user=user, type="signed_out")

    deleted = purge_old_security_events()

    assert deleted == 1
    assert set(SecurityEvent.objects.values_list("pk", flat=True)) == {
        edge.pk,
        fresh.pk,
    }
    assert not SecurityEvent.objects.filter(pk=old.pk).exists()


# --- a guard: nothing that ends a session or changes a credential forgets its event -----------
#
# ADR 0022: every place that ends a session, starts one, removes a key or changes a credential writes its event. The
# guard reads the source of the two modules that do these things. A function that calls one of the PRIMITIVES must
# either call ``events.record_*`` itself or call a function that does (a WRITER); otherwise it must be listed in EXEMPT
# with the reason. A new way to end a session therefore fails here until it writes an event or is exempted on purpose.

PRIMITIVES = {
    "end_session",  # ends one session
    "_end_sessions",  # ends all of a user's sessions
    "start_session",  # starts one
    "forget_device_keys",  # removes the keys
    "remove_key",  # removes one key
    "set_password",  # changes a credential
}

EXEMPT = {
    "services.end_session": "the primitive itself; its callers write the event",
    "services._end_sessions": "the primitive itself; reset and change write the event",
    "services._end_expired_session": "an expired session is not an event (ADR 0022)",
    "services.delete_account": "a deletion keeps nothing (ADR 0014); the events are deleted with the user",
    "services.register": "a registration creates no session; the sign-in that follows is recorded",
}


def _calls(function: ast.FunctionDef) -> tuple[set[str], set[str]]:
    """The names a function calls, and the ones of ``events.<name>`` among them."""
    plain: set[str] = set()
    from_events: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute):
            plain.add(target.attr)
            if isinstance(target.value, ast.Name) and target.value.id == "events":
                from_events.add(target.attr)
        elif isinstance(target, ast.Name):
            plain.add(target.id)
    return plain, from_events


def _functions(module: Any) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(Path(inspect.getsourcefile(module) or "").read_text())
    short = module.__name__.rsplit(".", 1)[1]
    return {
        f"{short}.{node.name}": node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }


def _all_functions() -> dict[str, ast.FunctionDef]:
    found: dict[str, ast.FunctionDef] = {}
    for module in (services, device_keys):
        found.update(_functions(module))
    return found


def test_every_function_that_ends_a_session_or_changes_a_credential_writes_an_event() -> (
    None
):
    functions = _all_functions()
    writers = {
        name.split(".", 1)[1]
        for name, function in functions.items()
        if any(call.startswith("record_") for call in _calls(function)[1])
    }
    missing = []
    for name, function in functions.items():
        plain, from_events = _calls(function)
        if not plain & PRIMITIVES or name in EXEMPT:
            continue
        if any(call.startswith("record_") for call in from_events):
            continue  # it writes the event itself
        if plain & PRIMITIVES & writers:
            continue  # it calls a function that writes it
        missing.append(name)

    assert missing == [], f"No security event is written by: {missing}"


def test_these_are_the_functions_that_write_events() -> None:
    """The list the guard relies on: a change to it is a change to what the trail records, so it is reviewed."""
    writers = {
        name
        for name, function in _all_functions().items()
        if any(call.startswith("record_") for call in _calls(function)[1])
    }

    assert writers == {
        "services.start_session",
        "services.sign_out",
        "services._rotate",
        "services.sign_out_with_refresh_token",
        "services.reset_password",
        "services.forget_device_keys",
        "services.change_password",
        "services.accept",  # the closure of confirm_email_change
        "services.confirm_email_change",  # the walk includes the closure inside it
        "device_keys.register_key",
        "device_keys.remove_key",
    }


def test_the_guard_notices_a_function_that_forgets_its_event() -> None:
    """The guard is only worth something if it fails on the bug it is for."""
    source = "def sneaky(session):\n    end_session(session, 'x')\n"
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    plain, from_events = _calls(function)

    assert plain & PRIMITIVES == {"end_session"}
    assert not from_events


def test_every_end_reason_is_accounted_for() -> None:
    """A new way to end a session must be added here, with the event it writes or the reason it writes none."""
    from users.models import EndReason

    written_now = {
        EndReason.SIGNED_OUT,  # signed_out
        EndReason.PASSWORD_CHANGED,  # password_changed
        EndReason.PASSWORD_RESET,  # password_reset
        EndReason.EMAIL_CHANGED,  # email_changed
        EndReason.TOKEN_REUSE,  # token_reuse_detected
        EndReason.REVOKED_BY_USER,  # session_revoked
        EndReason.SESSION_LIMIT,  # session_limit_reached
    }
    no_event_yet_or_by_decision = {
        EndReason.EXPIRED,  # not an event (ADR 0022)
        EndReason.ACCOUNT_DELETED,  # keeps nothing (ADR 0014)
        EndReason.REPLACED,  # a newer sign-in of the same device: the person did it, nothing to warn about
    }

    assert written_now | no_event_yet_or_by_decision == set(EndReason)
