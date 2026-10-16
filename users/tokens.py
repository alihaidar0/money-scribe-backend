"""The access and refresh tokens (ADR 0006): JSON Web Tokens signed with HS256.

The algorithm is pinned here and never read from a token. Expiry is checked against ``timezone.now()``
rather than inside the library, so the same clock drives the sessions and the tests.

The refresh token is **derived**, not random: its ``jti`` and ``iat`` come from the session id, the
rotation counter and the time of the rotation. A retry inside the replay window can therefore be
answered with the same token again without storing it; only its hash is stored.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import jwt
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from .models import Session

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "at+jwt"  # noqa: S105 - the JOSE header type, not a secret
REFRESH_TOKEN_KIND = "refresh"  # noqa: S105
MIN_KEY_BYTES = 32
_REQUIRED_CLAIMS = ["exp", "iat", "sub", "jti", "sid"]


class TokenError(Exception):
    """A token that cannot be used."""


class TokenExpired(TokenError):
    """A genuine token whose lifetime has passed."""


class TokenInvalid(TokenError):
    """Malformed, wrongly signed, of the wrong kind, or for another issuer or audience."""


@dataclass(frozen=True)
class AccessClaims:
    session_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class RefreshClaims:
    session_id: uuid.UUID
    user_id: uuid.UUID
    generation: int


def signing_keys() -> dict[str, str]:
    """The key ring: the current key under its id, then the previous ones that still verify."""
    key = settings.JWT_SIGNING_KEY
    if len(key.encode()) < MIN_KEY_BYTES:
        raise ImproperlyConfigured(
            f"JWT_SIGNING_KEY must be at least {MIN_KEY_BYTES} bytes long."
        )
    return {**settings.JWT_PREVIOUS_KEYS, settings.JWT_KEY_ID: key}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _timestamp(moment: datetime) -> int:
    return int(moment.timestamp())


def _encode(payload: dict[str, Any], *, header_type: str) -> str:
    return jwt.encode(
        payload,
        settings.JWT_SIGNING_KEY,
        algorithm=ALGORITHM,
        headers={"kid": settings.JWT_KEY_ID, "typ": header_type},
    )


def _base_claims(
    session: Session, issued_at: datetime, expires_at: datetime
) -> dict[str, Any]:
    return {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "sub": str(session.user_id),
        "sid": str(session.id),
        "iat": _timestamp(issued_at),
        "exp": _timestamp(expires_at),
    }


def issue_access_token(session: Session, now: datetime | None = None) -> str:
    now = now or timezone.now()
    lifetime = timedelta(seconds=settings.ACCESS_TOKEN_LIFETIME_SECONDS)
    payload = _base_claims(session, now, now + lifetime)
    payload["jti"] = uuid.uuid4().hex
    return _encode(payload, header_type=ACCESS_TOKEN_TYPE)


def refresh_token_lifetime_end(session: Session) -> datetime:
    """The refresh token lives 30 days from its rotation, never past the session's own limit."""
    lifetime = timedelta(days=settings.REFRESH_TOKEN_LIFETIME_DAYS)
    return min(session.rotated_at + lifetime, session.expires_at)


def issue_refresh_token(session: Session) -> str:
    """The refresh token of the session's current generation (the same one every time it is asked)."""
    payload = _base_claims(
        session, session.rotated_at, refresh_token_lifetime_end(session)
    )
    payload["jti"] = hmac.new(
        settings.JWT_SIGNING_KEY.encode(),
        f"refresh:{session.id}:{session.generation}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    payload["typ"] = REFRESH_TOKEN_KIND
    payload["gen"] = session.generation
    return _encode(payload, header_type="JWT")


def _decode(token: str, *, now: datetime, allow_expired: bool) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        key = signing_keys().get(str(header.get("kid")))
        if key is None:
            raise TokenInvalid
        payload: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={
                "require": _REQUIRED_CLAIMS,
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except jwt.PyJWTError as error:
        raise TokenInvalid from error
    payload["_header_type"] = header.get("typ")
    if not allow_expired and payload["exp"] <= now.timestamp():
        raise TokenExpired
    return payload


def _uuid(value: object) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise TokenInvalid from error


def decode_access_token(token: str, now: datetime | None = None) -> AccessClaims:
    payload = _decode(token, now=now or timezone.now(), allow_expired=False)
    if payload["_header_type"] != ACCESS_TOKEN_TYPE or "typ" in payload:
        raise TokenInvalid
    return AccessClaims(session_id=_uuid(payload["sid"]), user_id=_uuid(payload["sub"]))


def decode_refresh_token(
    token: str, now: datetime | None = None, *, allow_expired: bool = False
) -> RefreshClaims:
    """Verify a refresh token. ``allow_expired`` still checks the signature; it lets the caller end
    the session of a token whose lifetime has passed."""
    payload = _decode(token, now=now or timezone.now(), allow_expired=allow_expired)
    generation = payload.get("gen")
    if (
        payload.get("typ") != REFRESH_TOKEN_KIND
        or payload["_header_type"] == ACCESS_TOKEN_TYPE
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
    ):
        raise TokenInvalid
    return RefreshClaims(
        session_id=_uuid(payload["sid"]),
        user_id=_uuid(payload["sub"]),
        generation=generation,
    )
