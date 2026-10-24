import uuid
from datetime import timedelta

import jwt
import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from conftest import Clock
from users import tokens
from users.models import Session
from users.tests.factories import VerifiedUserFactory

pytestmark = pytest.mark.django_db


def make_session(**fields: object) -> Session:
    now = timezone.now()
    user = VerifiedUserFactory.create()
    session = Session(
        user=user,
        device_id=uuid.uuid4(),
        device_name="Pixel 8",
        platform="android",
        app_version="1.0.0+1",
        created_at=now,
        last_active_at=now,
        rotated_at=now,
        expires_at=now + timedelta(days=90),
        refresh_hash="x",
        **fields,
    )
    session.save()
    return session


def forge(
    payload: dict[str, object],
    *,
    key: str | None = None,
    algorithm: str = "HS256",
    headers: dict[str, object] | None = None,
) -> str:
    base = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": 1,
        "exp": 4_000_000_000,
        "jti": "j",
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
    }
    return jwt.encode(
        {**base, **payload},
        key or settings.JWT_SIGNING_KEY,
        algorithm=algorithm,
        headers={
            "kid": settings.JWT_KEY_ID,
            "typ": tokens.ACCESS_TOKEN_TYPE,
            **(headers or {}),
        },
    )


def test_an_access_token_carries_the_session_and_the_user(clock: Clock) -> None:
    session = make_session()

    claims = tokens.decode_access_token(tokens.issue_access_token(session))

    assert claims.session_id == session.id
    assert claims.user_id == session.user_id


def test_an_access_token_has_the_registered_claims_and_the_at_jwt_type(
    clock: Clock,
) -> None:
    session = make_session()
    token = tokens.issue_access_token(session)

    header = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False})

    assert header == {"alg": "HS256", "typ": "at+jwt", "kid": settings.JWT_KEY_ID}
    assert payload["iss"] == settings.JWT_ISSUER
    assert payload["aud"] == settings.JWT_AUDIENCE
    assert payload["sub"] == str(session.user_id)
    assert payload["sid"] == str(session.id)
    assert payload["exp"] - payload["iat"] == settings.ACCESS_TOKEN_LIFETIME_SECONDS
    assert payload["jti"]


def test_an_access_token_expires_by_the_clock(clock: Clock) -> None:
    token = tokens.issue_access_token(make_session())

    clock.advance(seconds=settings.ACCESS_TOKEN_LIFETIME_SECONDS - 1)
    tokens.decode_access_token(token)
    clock.advance(seconds=1)
    with pytest.raises(tokens.TokenExpired):
        tokens.decode_access_token(token)


@pytest.mark.parametrize(
    "token",
    [
        "not-a-token",
        "",
        "a.b.c",
    ],
)
def test_garbage_is_invalid(token: str) -> None:
    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(token)


def test_a_token_signed_with_another_key_is_invalid() -> None:
    token = forge({}, key="another-signing-key-0123456789abcdef-0123456789")

    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(token)


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
def test_the_algorithm_is_never_read_from_the_token() -> None:
    token = forge({}, algorithm="HS512")

    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(token)


def test_an_unsigned_token_is_invalid() -> None:
    payload = jwt.decode(forge({}), options={"verify_signature": False})
    unsigned = jwt.encode(
        payload,
        "",
        algorithm="none",
        headers={"kid": settings.JWT_KEY_ID, "typ": "at+jwt"},
    )

    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(unsigned)


@pytest.mark.parametrize(
    "payload",
    [
        {"aud": "someone-else"},
        {"iss": "someone-else"},
        {"sid": "not-a-uuid"},
        {"sub": "not-a-uuid"},
    ],
)
def test_wrong_audience_issuer_or_ids_are_invalid(payload: dict[str, object]) -> None:
    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(forge(payload))


def test_a_token_without_a_required_claim_is_invalid() -> None:
    payload = jwt.decode(forge({}), options={"verify_signature": False})
    del payload["sid"]
    token = jwt.encode(
        payload,
        settings.JWT_SIGNING_KEY,
        algorithm="HS256",
        headers={"kid": settings.JWT_KEY_ID, "typ": "at+jwt"},
    )

    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(token)


def test_an_unknown_key_id_is_invalid() -> None:
    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(forge({}, headers={"kid": "nope"}))


def test_a_refresh_token_is_not_an_access_token_and_the_reverse(clock: Clock) -> None:
    session = make_session()

    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(tokens.issue_refresh_token(session))
    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_refresh_token(tokens.issue_access_token(session))


def test_the_refresh_token_of_a_generation_is_always_the_same(clock: Clock) -> None:
    session = make_session()

    assert tokens.issue_refresh_token(session) == tokens.issue_refresh_token(session)


def test_the_refresh_token_changes_with_the_generation(clock: Clock) -> None:
    session = make_session()
    first = tokens.issue_refresh_token(session)
    session.generation = 1

    assert tokens.issue_refresh_token(session) != first
    assert tokens.decode_refresh_token(first).generation == 0


def test_a_refresh_token_lives_30_days_but_never_past_the_session(clock: Clock) -> None:
    session = make_session()
    payload = jwt.decode(
        tokens.issue_refresh_token(session), options={"verify_signature": False}
    )
    assert (
        payload["exp"] - payload["iat"] == settings.REFRESH_TOKEN_LIFETIME_DAYS * 86400
    )

    session.expires_at = session.rotated_at + timedelta(days=3)
    payload = jwt.decode(
        tokens.issue_refresh_token(session), options={"verify_signature": False}
    )
    assert payload["exp"] - payload["iat"] == 3 * 86400


def test_an_expired_refresh_token_can_still_be_read_when_asked(clock: Clock) -> None:
    session = make_session()
    token = tokens.issue_refresh_token(session)
    clock.advance(days=31)

    with pytest.raises(tokens.TokenExpired):
        tokens.decode_refresh_token(token)
    assert (
        tokens.decode_refresh_token(token, allow_expired=True).session_id == session.id
    )


def test_hash_is_sha256_hex() -> None:
    assert (
        tokens.hash_token("abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_a_previous_key_still_verifies_until_it_is_dropped(settings: object) -> None:
    old_key = "the-old-signing-key-0123456789abcdef-0123456789"
    token = forge({}, key=old_key, headers={"kid": "k0"})

    with pytest.raises(tokens.TokenInvalid):
        tokens.decode_access_token(token)
    settings.JWT_PREVIOUS_KEYS = {"k0": old_key}  # type: ignore[attr-defined]
    assert tokens.decode_access_token(token).session_id


def test_a_short_signing_key_is_refused(settings: object) -> None:
    settings.JWT_SIGNING_KEY = "too-short"  # type: ignore[attr-defined]

    with pytest.raises(ImproperlyConfigured):
        tokens.signing_keys()
