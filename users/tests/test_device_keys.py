"""Sign-in with a device key pair and a signed challenge (ADR 0018), through the API."""

import base64
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from rest_framework.test import APIClient

from conftest import Clock
from users import attestation, device_keys, limits
from users.models import DeviceKey, Session, User
from users.tests.device_key_helpers import (
    CERT_DIGEST,
    KEYS,
    PACKAGE,
    Phone,
    Spec,
    ask_challenge,
    b64,
    build_chain,
    key_sign_in,
    register,
    registration_body,
    sign_in_body,
)
from users.tests.factories import VerifiedUserFactory
from users.tests.helpers import (
    AUTH,
    DEVICE_ID,
    PASSWORD,
    bearer,
    device,
    emailed_code,
    first_error,
    sign_in,
)
from users.tests.test_limits import BrokenCache

pytestmark = pytest.mark.django_db

OTHER_DEVICE = "6f1d2c3b-4a59-4e8f-8b7a-0c9d1e2f3a4b"
NEW_PASSWORD = "a-new-passphrase-4321"


@dataclass
class Account:
    user: User
    access: str
    device_id: str = DEVICE_ID
    platform: str = "ios"


def open_account(
    client: APIClient,
    *,
    device_id: str = DEVICE_ID,
    platform: str = "ios",
    **user_fields: Any,
) -> Account:
    """A verified user signed in on a device, as the app is when it turns fingerprint sign-in on."""
    user = VerifiedUserFactory.create(**user_fields)
    return sign_in_account(client, user, device_id=device_id, platform=platform)


def sign_in_account(
    client: APIClient, user: User, *, device_id: str, platform: str = "ios"
) -> Account:
    response = sign_in(
        client, user.email, device=device(device_id=device_id, platform=platform)
    )
    assert response.status_code == 200, response.json()
    return Account(user, response.json()["access_token"], device_id, platform)


def enroll(
    client: APIClient, account: Account, phone: Phone, **chain: Any
) -> dict[str, Any]:
    """Register ``phone``'s key for the account's device; returns the key entry."""
    issued = ask_challenge(client, account.device_id)
    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=account.device_id,
            user_id=str(account.user.id),
            **chain,
        ),
    )
    assert response.status_code in (200, 201), response.json()
    entry: dict[str, Any] = response.json()
    return entry


@pytest.fixture
def phone() -> Phone:
    return Phone()


@pytest.fixture
def client(api_client: APIClient) -> APIClient:
    api_client.raise_request_exception = False
    return api_client


@pytest.fixture
def redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "cache", BrokenCache())
    monkeypatch.setattr(device_keys, "cache", BrokenCache())


# --- the challenge ----------------------------------------------------------------------------


def test_a_challenge_is_32_random_bytes_that_expire_in_two_minutes(
    client: APIClient,
) -> None:
    response = client.post(f"{KEYS}/challenge/", {"device_id": DEVICE_ID})

    body = response.json()
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert uuid.UUID(body["challenge_id"]).version == 4
    assert body["expires_in"] == 120
    assert len(device_keys.challenge_bytes(body["challenge"])) == 32
    assert "=" not in body["challenge"]


def test_every_challenge_is_new(client: APIClient) -> None:
    first = ask_challenge(client, DEVICE_ID)
    second = ask_challenge(client, DEVICE_ID)

    assert first["challenge"] != second["challenge"]
    assert first["challenge_id"] != second["challenge_id"]


def test_a_known_and_an_unknown_device_get_the_same_shape(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    enroll(client, account, phone)

    known = ask_challenge(client, DEVICE_ID)
    unknown = ask_challenge(client, OTHER_DEVICE)

    assert known.keys() == unknown.keys()
    assert len(known["challenge"]) == len(unknown["challenge"])


def test_a_challenge_needs_no_token_and_a_bad_device_id_is_a_400(
    client: APIClient,
) -> None:
    bad = client.post(f"{KEYS}/challenge/", {"device_id": "not-a-uuid"})
    missing = client.post(f"{KEYS}/challenge/", {})

    assert bad.status_code == missing.status_code == 400
    assert first_error(bad)["attr"] == "device_id"


def test_challenges_are_limited_per_client_address(client: APIClient) -> None:
    for _ in range(60):
        assert (
            client.post(f"{KEYS}/challenge/", {"device_id": DEVICE_ID}).status_code
            == 200
        )

    response = client.post(f"{KEYS}/challenge/", {"device_id": DEVICE_ID})

    assert response.status_code == 429
    assert first_error(response)["code"] == "throttled"
    assert int(response["Retry-After"]) >= 1


def test_a_challenge_is_a_503_when_the_store_is_down(
    client: APIClient, redis_down: None
) -> None:
    response = client.post(f"{KEYS}/challenge/", {"device_id": DEVICE_ID})

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"


# --- registering a key ------------------------------------------------------------------------


def test_a_key_is_registered_with_a_proof_that_the_phone_holds_it(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)

    entry = enroll(client, account, phone)

    key = DeviceKey.objects.get()
    assert entry == {
        "id": str(key.id),
        "device_id": DEVICE_ID,
        "device_name": "Pixel 8",
        "platform": "ios",
        "attested": False,
        "is_current": True,
        "created_at": entry["created_at"],
        "last_used_at": None,
        "expires_at": entry["expires_at"],
    }
    assert key.user == account.user
    assert bytes(key.public_key) == base64.b64decode(phone.spki_b64)
    assert key.expires_at - key.created_at == timedelta(days=30)


def test_the_first_registration_is_201_and_a_replacement_is_200(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    first = ask_challenge(client, DEVICE_ID)
    created = register(
        client,
        account.access,
        registration_body(
            phone, first, device_id=DEVICE_ID, user_id=str(account.user.id)
        ),
    )
    newer = Phone()
    second = ask_challenge(client, DEVICE_ID)
    replaced = register(
        client,
        account.access,
        registration_body(
            newer, second, device_id=DEVICE_ID, user_id=str(account.user.id)
        ),
    )

    assert (created.status_code, replaced.status_code) == (201, 200)
    assert DeviceKey.objects.count() == 1
    assert bytes(DeviceKey.objects.get().public_key) == base64.b64decode(newer.spki_b64)


def test_the_raw_point_of_the_secure_enclave_is_accepted_and_stored_as_spki(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)

    enroll(client, account, phone, public_key=phone.raw_point_b64)

    assert bytes(DeviceKey.objects.get().public_key) == base64.b64decode(phone.spki_b64)


def test_the_owner_is_emailed_that_a_device_can_sign_in_with_a_fingerprint(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    mail.outbox.clear()

    enroll(client, account, phone)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [account.user.email]
    assert "Pixel 8" in mail.outbox[0].body
    assert "http" not in mail.outbox[0].body


def test_registration_needs_a_token(client: APIClient, phone: Phone) -> None:
    response = client.put(f"{KEYS}/current/", {})

    assert response.status_code == 401


def test_a_wrong_password_is_a_400_on_current_password(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            password="wrong-password-123",
        ),
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "invalid_password"
    assert first_error(response)["attr"] == "current_password"
    assert DeviceKey.objects.count() == 0


def test_the_password_lock_is_shared_with_the_password_change(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    for _ in range(5):
        issued = ask_challenge(client, DEVICE_ID)
        response = register(
            client,
            account.access,
            registration_body(
                phone,
                issued,
                device_id=DEVICE_ID,
                user_id=str(account.user.id),
                password="wrong-password-123",
            ),
        )

    locked = client.post(
        f"{AUTH}/password/change/",
        {"current_password": PASSWORD, "password": NEW_PASSWORD},
        headers=bearer(account.access),
    )

    assert response.status_code == 429
    assert first_error(response)["code"] == "password_attempts_exceeded"
    assert locked.status_code == 429


@pytest.mark.parametrize(
    "change",
    [
        {"challenge_id": str(uuid.uuid4())},
        {"challenge_id": "not-a-uuid"},
    ],
)
def test_an_unknown_challenge_is_refused(
    client: APIClient, phone: Phone, change: dict[str, Any]
) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone, issued, device_id=DEVICE_ID, user_id=str(account.user.id), **change
        ),
    )

    assert response.status_code == 400
    assert first_error(response)["attr"] == "challenge_id"


def test_a_challenge_works_once(client: APIClient, phone: Phone) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)
    body = registration_body(
        phone, issued, device_id=DEVICE_ID, user_id=str(account.user.id)
    )
    first = register(client, account.access, body)

    again = register(client, account.access, body)

    assert first.status_code == 201
    assert again.status_code == 400
    assert first_error(again)["code"] == "challenge_invalid"


def test_a_challenge_that_expired_is_refused(client: APIClient, phone: Phone) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)
    cache.delete(
        f"devicekey:challenge:{issued['challenge_id']}"
    )  # what the cache does at 120 seconds

    response = register(
        client,
        account.access,
        registration_body(
            phone, issued, device_id=DEVICE_ID, user_id=str(account.user.id)
        ),
    )

    assert first_error(response)["code"] == "challenge_invalid"


def test_a_challenge_asked_for_another_device_is_refused(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    elsewhere = ask_challenge(client, OTHER_DEVICE)

    response = register(
        client,
        account.access,
        registration_body(
            phone, elsewhere, device_id=DEVICE_ID, user_id=str(account.user.id)
        ),
    )

    assert first_error(response)["code"] == "challenge_invalid"


def test_a_signature_by_another_key_is_not_a_proof(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)
    impostor = Phone()
    body = registration_body(
        impostor, issued, device_id=DEVICE_ID, user_id=str(account.user.id)
    )
    body["public_key"] = (
        phone.spki_b64
    )  # the key of someone else, signed for by the impostor

    response = register(client, account.access, body)

    assert first_error(response)["code"] == "challenge_invalid"
    assert DeviceKey.objects.count() == 0


def test_a_sign_in_signature_cannot_register_a_key(
    client: APIClient, phone: Phone
) -> None:
    """The two texts begin with different lines, so a signature for one is never good for the other."""
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)
    message = device_keys.sign_in_message(
        uuid.UUID(issued["challenge_id"]), issued["challenge"], uuid.UUID(DEVICE_ID)
    )

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            signature=phone.sign(message),
        ),
    )

    assert first_error(response)["code"] == "challenge_invalid"


def test_a_signature_made_for_another_account_is_refused(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone, issued, device_id=DEVICE_ID, user_id=str(uuid.uuid4())
        ),
    )

    assert first_error(response)["code"] == "challenge_invalid"


@pytest.mark.parametrize(
    "key",
    [
        "AAAA",  # three zero bytes
        b64(
            b"\x04" + bytes(64)
        ),  # 65 bytes that look like a point and are not on the curve
        b64(bytes(91)),  # the size of an SPKI, but not one
    ],
    ids=["too-short", "point-off-curve", "not-spki"],
)
def test_something_that_is_not_a_p256_key_is_refused(
    client: APIClient, phone: Phone, key: str
) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            public_key=key,
        ),
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "public_key_invalid"
    assert first_error(response)["attr"] == "public_key"


def test_a_p384_key_is_refused(client: APIClient) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    account = open_account(client)
    big = Phone(ec.SECP384R1())

    issued = ask_challenge(client, DEVICE_ID)
    response = register(
        client,
        account.access,
        registration_body(
            big, issued, device_id=DEVICE_ID, user_id=str(account.user.id)
        ),
    )

    assert first_error(response)["code"] == "public_key_invalid"


def test_a_refused_key_still_spends_the_challenge(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)
    register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            public_key="AAAA",
        ),
    )

    again = register(
        client,
        account.access,
        registration_body(
            phone, issued, device_id=DEVICE_ID, user_id=str(account.user.id)
        ),
    )

    assert first_error(again)["code"] == "challenge_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("public_key", "not base64!"),
        ("signature", "not base64!"),
        ("signature", ""),
        ("algorithm", "RS256"),
        ("attestation_chain", ["x"] * 11),
    ],
)
def test_a_malformed_field_is_a_400_on_that_field(
    client: APIClient, phone: Phone, field: str, value: Any
) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            **{field: value},
        ),
    )

    assert response.status_code == 400
    assert first_error(response)["attr"].split(".")[0] == field


def test_a_web_session_cannot_register_a_key(client: APIClient, phone: Phone) -> None:
    account = open_account(client, platform="web")
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone, issued, device_id=DEVICE_ID, user_id=str(account.user.id)
        ),
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "platform_not_supported"
    assert first_error(response)["attr"] is None


def test_an_account_holds_five_keys(client: APIClient) -> None:
    user = VerifiedUserFactory.create()
    devices = [str(uuid.uuid4()) for _ in range(6)]
    accounts = [sign_in_account(client, user, device_id=d) for d in devices]
    for account in accounts[:5]:
        enroll(client, account, Phone())

    issued = ask_challenge(client, devices[5])
    sixth = register(
        client,
        accounts[5].access,
        registration_body(Phone(), issued, device_id=devices[5], user_id=str(user.id)),
    )
    again = enroll(
        client, accounts[0], Phone()
    )  # replacing one that exists is not a sixth

    assert sixth.status_code == 409
    assert first_error(sixth)["code"] == "device_key_limit_reached"
    assert first_error(sixth)["attr"] is None
    assert again["device_id"] == devices[0]
    assert DeviceKey.objects.filter(user=user).count() == 5


def test_another_account_taking_a_device_replaces_the_key_it_held(
    client: APIClient,
) -> None:
    first = open_account(client)
    enroll(client, first, Phone())
    second = open_account(client)  # the phone changed hands: same device id

    entry = enroll(client, second, Phone())

    assert entry["device_id"] == DEVICE_ID
    assert list(DeviceKey.objects.values_list("user_id", flat=True)) == [second.user.id]


def test_registration_is_a_503_when_the_store_is_down(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    issued = ask_challenge(client, DEVICE_ID)
    body = registration_body(
        phone, issued, device_id=DEVICE_ID, user_id=str(account.user.id)
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(limits, "cache", BrokenCache())
        response = register(client, account.access, body)

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"


# --- an Android key and its attestation -------------------------------------------------------


@pytest.fixture
def android_trust(
    settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> set[str]:
    settings.DEVICE_KEY_ANDROID_PACKAGE = PACKAGE
    settings.DEVICE_KEY_ANDROID_CERT_SHA256 = [CERT_DIGEST.hex()]
    revoked: set[str] = set()
    monkeypatch.setattr(attestation, "_fetch_status", lambda: revoked)
    return revoked


def android_chain(
    phone: Phone, issued: dict[str, Any], tmp_path: Path, settings: Any, **spec: Any
) -> list[str]:
    chain = build_chain(
        phone,
        Spec(challenge=device_keys.challenge_bytes(issued["challenge"]), **spec),
    )
    roots = tmp_path / "roots.pem"
    roots.write_text(chain.root_pem)
    settings.DEVICE_KEY_TRUSTED_ROOTS_PATH = roots
    return chain.b64


def test_an_attested_android_key_is_registered_and_flagged(
    client: APIClient,
    phone: Phone,
    android_trust: set[str],
    tmp_path: Path,
    settings: Any,
) -> None:
    account = open_account(client, platform="android")
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            chain=android_chain(phone, issued, tmp_path, settings),
        ),
    )

    assert response.status_code == 201
    assert response.json()["attested"] is True
    assert response.json()["platform"] == "android"
    assert DeviceKey.objects.get().attested is True


def test_an_android_key_without_a_chain_is_refused(
    client: APIClient, phone: Phone, android_trust: set[str]
) -> None:
    account = open_account(client, platform="android")
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone, issued, device_id=DEVICE_ID, user_id=str(account.user.id)
        ),
    )

    assert response.status_code == 400
    assert first_error(response)["code"] == "attestation_invalid"
    assert first_error(response)["attr"] == "attestation_chain"
    assert DeviceKey.objects.count() == 0


def test_an_android_key_that_fails_a_rule_is_refused_without_a_reason(
    client: APIClient,
    phone: Phone,
    android_trust: set[str],
    tmp_path: Path,
    settings: Any,
) -> None:
    account = open_account(client, platform="android")
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            chain=android_chain(phone, issued, tmp_path, settings, locked=False),
        ),
    )

    error = first_error(response)
    assert response.status_code == 400
    assert error["code"] == "attestation_invalid"
    assert "boot" not in error["detail"].lower()
    assert "locked" not in error["detail"].lower()


def test_the_challenge_of_the_attestation_must_be_the_one_asked_for(
    client: APIClient,
    phone: Phone,
    android_trust: set[str],
    tmp_path: Path,
    settings: Any,
) -> None:
    account = open_account(client, platform="android")
    issued = ask_challenge(client, DEVICE_ID)
    stale = ask_challenge(
        client, DEVICE_ID
    )  # an attestation made for another challenge

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            chain=android_chain(phone, stale, tmp_path, settings),
        ),
    )

    assert first_error(response)["code"] == "attestation_invalid"


def test_a_revoked_attestation_key_is_refused(
    client: APIClient,
    phone: Phone,
    android_trust: set[str],
    tmp_path: Path,
    settings: Any,
) -> None:
    account = open_account(client, platform="android")
    issued = ask_challenge(client, DEVICE_ID)
    android_trust.add("c3")  # the serial of the leaf the helper builds

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            chain=android_chain(phone, issued, tmp_path, settings),
        ),
    )

    assert first_error(response)["code"] == "attestation_invalid"


def test_a_revocation_list_that_cannot_be_reached_is_a_503(
    client: APIClient,
    phone: Phone,
    android_trust: set[str],
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down() -> set[str]:
        raise OSError("unreachable")

    monkeypatch.setattr(attestation, "_fetch_status", down)
    account = open_account(client, platform="android")
    issued = ask_challenge(client, DEVICE_ID)

    response = register(
        client,
        account.access,
        registration_body(
            phone,
            issued,
            device_id=DEVICE_ID,
            user_id=str(account.user.id),
            chain=android_chain(phone, issued, tmp_path, settings),
        ),
    )

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"
    assert DeviceKey.objects.count() == 0


def test_development_may_skip_the_attestation(
    client: APIClient, phone: Phone, settings: Any
) -> None:
    settings.DEVICE_KEY_REQUIRE_ATTESTATION = False
    account = open_account(client, platform="android")

    entry = enroll(client, account, phone)

    assert entry["attested"] is False


def test_an_ios_key_is_accepted_unattested_and_a_chain_is_ignored(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client, platform="ios")

    entry = enroll(client, account, phone, chain=["AAAA"])

    assert entry["attested"] is False


# --- signing in with the key ------------------------------------------------------------------


def test_a_phone_signs_in_with_its_key_and_gets_the_tokens_of_sign_in(
    client: APIClient, phone: Phone, clock: Clock
) -> None:
    account = open_account(client, language="en", time_zone="UTC")
    enroll(client, account, phone)
    client.post(
        f"{AUTH}/sign-out/", headers=bearer(account.access)
    )  # signing out keeps the key

    response = key_sign_in(
        client, phone, DEVICE_ID, language="ar", time_zone="Asia/Riyadh"
    )

    body = response.json()
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 600
    assert body["user"] == {
        "id": str(account.user.id),
        "name": account.user.name,
        "email": account.user.email,
    }
    session = Session.objects.filter(user=account.user, ended_at__isnull=True).get()
    assert str(session.device_id) == DEVICE_ID
    assert session.platform == "ios"
    account.user.refresh_from_db()
    assert (account.user.language, account.user.time_zone) == ("ar", "Asia/Riyadh")
    me = client.get(f"{AUTH}/profile/", headers=bearer(body["access_token"]))
    assert me.status_code == 200


def test_a_sign_in_renews_the_key_for_thirty_more_days(
    client: APIClient, phone: Phone, clock: Clock
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    clock.advance(days=20)

    key_sign_in(client, phone, DEVICE_ID)

    key = DeviceKey.objects.get()
    assert key.last_used_at == clock.current
    assert key.expires_at == clock.current + timedelta(days=30)


def test_signing_out_does_not_remove_the_key(client: APIClient, phone: Phone) -> None:
    account = open_account(client)
    enroll(client, account, phone)

    client.post(f"{AUTH}/sign-out/", headers=bearer(account.access))

    assert DeviceKey.objects.count() == 1


def failed_sign_in(response: Any) -> None:
    assert response.status_code == 401, response.json()
    assert first_error(response)["code"] == "device_sign_in_failed"


def test_every_failure_is_the_same_401(client: APIClient, phone: Phone) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    answers = []

    # the right phone, then a phone nobody registered, an unknown device, a junk signature
    unknown_challenge = key_sign_in(
        client, phone, DEVICE_ID, challenge_id=str(uuid.uuid4())
    )
    answers.append(unknown_challenge)
    answers.append(key_sign_in(client, Phone(), DEVICE_ID))  # another key signs
    answers.append(key_sign_in(client, phone, OTHER_DEVICE))  # a device with no key
    answers.append(key_sign_in(client, phone, DEVICE_ID, signature="AAAA"))

    for response in answers:
        failed_sign_in(response)
    assert len({str(r.json()) for r in answers}) == 1
    assert all(r["WWW-Authenticate"] == "Bearer" for r in answers)


def test_a_challenge_works_once_at_sign_in(client: APIClient, phone: Phone) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    issued = ask_challenge(client, DEVICE_ID)
    body = sign_in_body(phone, issued, device_id=DEVICE_ID)
    first = client.post(f"{KEYS}/sign-in/", body)

    replay = client.post(f"{KEYS}/sign-in/", body)

    assert first.status_code == 200
    failed_sign_in(replay)


def test_a_challenge_asked_for_another_device_does_not_sign_in(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    elsewhere = ask_challenge(client, OTHER_DEVICE)

    response = client.post(
        f"{KEYS}/sign-in/", sign_in_body(phone, elsewhere, device_id=DEVICE_ID)
    )

    failed_sign_in(response)


def test_the_registration_text_does_not_sign_in(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    issued = ask_challenge(client, DEVICE_ID)
    message = device_keys.registration_message(
        uuid.UUID(issued["challenge_id"]),
        issued["challenge"],
        uuid.UUID(DEVICE_ID),
        account.user.id,
    )

    response = client.post(
        f"{KEYS}/sign-in/",
        sign_in_body(phone, issued, device_id=DEVICE_ID, signature=phone.sign(message)),
    )

    failed_sign_in(response)


def test_an_expired_key_does_not_sign_in(
    client: APIClient, phone: Phone, clock: Clock
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    clock.advance(days=30, seconds=1)

    response = key_sign_in(client, phone, DEVICE_ID)

    failed_sign_in(response)


def test_an_inactive_account_does_not_sign_in(client: APIClient, phone: Phone) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    User.objects.filter(pk=account.user.pk).update(is_active=False)

    failed_sign_in(key_sign_in(client, phone, DEVICE_ID))


def test_an_account_whose_email_is_not_verified_does_not_sign_in(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    User.objects.filter(pk=account.user.pk).update(email_verified_at=None)

    failed_sign_in(key_sign_in(client, phone, DEVICE_ID))


def test_a_key_of_one_platform_does_not_sign_in_as_another(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client, platform="ios")
    enroll(client, account, phone)

    failed_sign_in(key_sign_in(client, phone, DEVICE_ID, platform="android"))


def test_a_malformed_body_is_a_400_not_a_failure(client: APIClient) -> None:
    response = client.post(f"{KEYS}/sign-in/", {"challenge_id": "x", "signature": "!"})

    assert response.status_code == 400


# --- the lock of the key sign-in --------------------------------------------------------------


def test_five_failures_from_a_device_lock_it_even_for_the_right_key(
    client: APIClient, phone: Phone, clock: Clock
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    for _ in range(4):
        failed_sign_in(key_sign_in(client, Phone(), DEVICE_ID))

    fifth = key_sign_in(client, Phone(), DEVICE_ID)
    right_key = key_sign_in(client, phone, DEVICE_ID)

    for response in (fifth, right_key):
        assert response.status_code == 429
        assert first_error(response)["code"] == "sign_in_locked"
        assert int(response["Retry-After"]) >= 1
    clock.advance(seconds=901)
    assert key_sign_in(client, phone, DEVICE_ID).status_code == 200


def test_a_success_starts_the_count_of_the_device_again(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    for _ in range(4):
        failed_sign_in(key_sign_in(client, Phone(), DEVICE_ID))
    assert key_sign_in(client, phone, DEVICE_ID).status_code == 200

    for _ in range(4):
        failed_sign_in(key_sign_in(client, Phone(), DEVICE_ID))

    assert key_sign_in(client, phone, DEVICE_ID).status_code == 200


def test_twenty_failures_from_an_address_lock_every_device_of_it(
    client: APIClient, phone: Phone, clock: Clock
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    for number in range(19):
        failed_sign_in(key_sign_in(client, Phone(), str(uuid.uuid4())))
        if number % 8 == 7:
            clock.advance(
                seconds=61
            )  # the per-minute rate limit is another brake, tested elsewhere

    twentieth = key_sign_in(client, Phone(), str(uuid.uuid4()))
    right_key = key_sign_in(client, phone, DEVICE_ID)

    assert twentieth.status_code == 429
    assert right_key.status_code == 429
    assert first_error(right_key)["code"] == "sign_in_locked"


def test_the_key_sign_in_is_also_limited_per_minute(client: APIClient) -> None:
    for _ in range(20):
        client.post(
            f"{KEYS}/sign-in/",
            {
                "challenge_id": str(uuid.uuid4()),
                "signature": "AAAA",
                "device": device(platform="ios"),
            },
        )

    response = client.post(
        f"{KEYS}/sign-in/",
        {
            "challenge_id": str(uuid.uuid4()),
            "signature": "AAAA",
            "device": device(platform="ios"),
        },
    )

    assert response.status_code == 429
    assert first_error(response)["code"] in {"throttled", "sign_in_locked"}
    assert int(response["Retry-After"]) >= 1


def test_a_locked_password_sign_in_does_not_stop_a_phone_with_a_key(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    for _ in range(5):
        sign_in(client, account.user.email, password="wrong-password-123")
    assert sign_in(client, account.user.email).status_code == 429

    response = key_sign_in(client, phone, DEVICE_ID)

    assert response.status_code == 200


def test_failed_key_sign_ins_do_not_lock_the_password_sign_in(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    enroll(client, account, phone)
    for _ in range(5):
        key_sign_in(client, Phone(), DEVICE_ID)

    response = sign_in(client, account.user.email)

    assert response.status_code == 200


def test_the_key_sign_in_is_a_503_when_the_store_is_down(
    client: APIClient, phone: Phone, redis_down: None
) -> None:
    response = client.post(
        f"{KEYS}/sign-in/",
        {
            "challenge_id": str(uuid.uuid4()),
            "signature": "AAAA",
            "device": device(platform="ios"),
        },
    )

    assert response.status_code == 503
    assert first_error(response)["code"] == "service_unavailable"
    assert int(response["Retry-After"]) >= 1


# --- the list and the removal -----------------------------------------------------------------


def test_the_list_of_an_account_with_no_key_is_empty(client: APIClient) -> None:
    account = open_account(client)

    response = client.get(f"{KEYS}/", headers=bearer(account.access))

    assert response.status_code == 200
    assert response.json() == {"next": None, "previous": None, "results": []}


def test_the_list_needs_a_token(client: APIClient) -> None:
    assert client.get(f"{KEYS}/").status_code == 401


def test_the_list_shows_only_the_own_keys_newest_first_and_marks_the_current(
    client: APIClient, clock: Clock
) -> None:
    user = VerifiedUserFactory.create()
    here = sign_in_account(client, user, device_id=DEVICE_ID)
    there = sign_in_account(client, user, device_id=OTHER_DEVICE)
    enroll(client, there, Phone())
    clock.advance(seconds=5)
    enroll(client, here, Phone())
    stranger = open_account(client, device_id=str(uuid.uuid4()))
    enroll(client, stranger, Phone())

    response = client.get(f"{KEYS}/", headers=bearer(here.access))

    results = response.json()["results"]
    assert [(r["device_id"], r["is_current"]) for r in results] == [
        (DEVICE_ID, True),
        (OTHER_DEVICE, False),
    ]


def test_a_key_is_removed_from_this_device_and_again_is_not_an_error(
    client: APIClient, phone: Phone
) -> None:
    account = open_account(client)
    enroll(client, account, phone)

    first = client.delete(f"{KEYS}/current/", headers=bearer(account.access))
    second = client.delete(f"{KEYS}/current/", headers=bearer(account.access))

    assert (first.status_code, second.status_code) == (204, 204)
    assert DeviceKey.objects.count() == 0
    failed_sign_in(key_sign_in(client, phone, DEVICE_ID))


def test_a_key_of_another_device_is_removed_by_its_device_id(
    client: APIClient,
) -> None:
    user = VerifiedUserFactory.create()
    here = sign_in_account(client, user, device_id=DEVICE_ID)
    there = sign_in_account(client, user, device_id=OTHER_DEVICE)
    enroll(client, there, Phone())

    response = client.delete(f"{KEYS}/{OTHER_DEVICE}/", headers=bearer(here.access))

    assert response.status_code == 204
    assert DeviceKey.objects.count() == 0


def test_another_users_key_cannot_be_removed_and_the_answer_hides_it(
    client: APIClient, phone: Phone
) -> None:
    owner = open_account(client)
    enroll(client, owner, phone)
    stranger = open_account(client, device_id=OTHER_DEVICE)

    real = client.delete(f"{KEYS}/{DEVICE_ID}/", headers=bearer(stranger.access))
    unknown = client.delete(f"{KEYS}/{uuid.uuid4()}/", headers=bearer(stranger.access))

    assert real.status_code == unknown.status_code == 204
    assert real.content == unknown.content
    assert DeviceKey.objects.count() == 1


def test_the_removal_needs_a_token(client: APIClient) -> None:
    assert client.delete(f"{KEYS}/current/").status_code == 401
    assert client.delete(f"{KEYS}/{DEVICE_ID}/").status_code == 401


# --- what removes every key -------------------------------------------------------------------


def two_phones(client: APIClient) -> tuple[Account, Phone, Phone]:
    user = VerifiedUserFactory.create()
    here = sign_in_account(client, user, device_id=DEVICE_ID)
    there = sign_in_account(client, user, device_id=OTHER_DEVICE)
    mine, theirs = Phone(), Phone()
    enroll(client, here, mine)
    enroll(client, there, theirs)
    return here, mine, theirs


def test_a_password_change_removes_every_key_but_keeps_the_session(
    client: APIClient,
) -> None:
    here, mine, theirs = two_phones(client)

    response = client.post(
        f"{AUTH}/password/change/",
        {"current_password": PASSWORD, "password": NEW_PASSWORD},
        headers=bearer(here.access),
    )

    assert response.status_code == 200
    assert DeviceKey.objects.count() == 0
    failed_sign_in(key_sign_in(client, mine, DEVICE_ID))
    failed_sign_in(key_sign_in(client, theirs, OTHER_DEVICE))


def test_a_password_reset_removes_every_key(client: APIClient) -> None:
    here = two_phones(client)[0]
    email = here.user.email
    client.post(f"{AUTH}/password-reset/request/", {"email": email})
    verified = client.post(
        f"{AUTH}/password-reset/verify/", {"email": email, "code": emailed_code()}
    )

    done = client.post(
        f"{AUTH}/password-reset/confirm/",
        {"reset_token": verified.json()["reset_token"], "password": NEW_PASSWORD},
    )

    assert done.status_code == 204
    assert DeviceKey.objects.count() == 0


def test_an_email_change_removes_every_key(client: APIClient) -> None:
    here = two_phones(client)[0]
    started = client.post(
        f"{AUTH}/email-change/",
        {"email": "new-address@example.com", "current_password": PASSWORD},
        headers=bearer(here.access),
    )
    assert started.status_code == 200

    confirmed = client.post(
        f"{AUTH}/email-change/confirm/",
        {"code": emailed_code()},
        headers=bearer(here.access),
    )

    assert confirmed.status_code == 200
    assert DeviceKey.objects.count() == 0


def test_deleting_the_account_removes_every_key(client: APIClient) -> None:
    here = two_phones(client)[0]

    response = client.post(
        f"{AUTH}/account/delete/", {"password": PASSWORD}, headers=bearer(here.access)
    )

    assert response.status_code == 204
    assert DeviceKey.objects.count() == 0


def test_no_challenge_signature_or_key_reaches_a_log(
    client: APIClient, phone: Phone, caplog: pytest.LogCaptureFixture
) -> None:
    account = open_account(client)
    with caplog.at_level("DEBUG"):
        enrolled = ask_challenge(client, DEVICE_ID)
        body = registration_body(
            phone, enrolled, device_id=DEVICE_ID, user_id=str(account.user.id)
        )
        register(client, account.access, body)
        issued = ask_challenge(client, DEVICE_ID)
        signed = sign_in_body(phone, issued, device_id=DEVICE_ID)
        client.post(f"{KEYS}/sign-in/", signed)
        client.post(f"{KEYS}/sign-in/", signed)  # a replay, which fails and is counted

    for secret in (
        enrolled["challenge"],
        issued["challenge"],
        body["signature"],
        signed["signature"],
        phone.spki_b64,
    ):
        assert secret not in caplog.text
