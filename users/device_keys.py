"""Sign-in with a device key pair and a signed challenge (ADR 0018), free of HTTP.

The phone holds a hardware key that needs a fingerprint for every use. The server holds only its public half.
To sign in, the phone asks for a challenge (32 random bytes, valid two minutes, single use), signs a fixed text
that contains it, and sends the signature; the server checks it against the stored public key. To register a
key, the phone proves it holds the private half the same way, and an Android phone also sends a key attestation.

The challenge lives in the cache and is claimed with one delete, so two requests with the same challenge cannot
both win. A challenge, a signature, a public key and an attestation chain are never logged.
"""

import base64
import functools
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.exceptions import ErrorDetail, ValidationError

from config.error_codes import ErrorCode
from config.exceptions import conflict

from . import attestation, emails, limits, services
from .models import DeviceKey, Platform, Session, User
from .network import rate_limit_key

logger = logging.getLogger(__name__)

CONTEXT_SIGN_IN = "money-scribe/device-sign-in/v1"
CONTEXT_REGISTRATION = "money-scribe/device-key-registration/v1"
CHALLENGE_BYTES = 32
UNCOMPRESSED_POINT_LENGTH = 65


@dataclass(frozen=True)
class IssuedChallenge:
    challenge_id: uuid.UUID
    challenge: str
    expires_in: int


@dataclass(frozen=True)
class Registered:
    key: DeviceKey
    created: bool


@dataclass(frozen=True)
class _Challenge:
    challenge: str
    device_id: uuid.UUID


# --- the text that is signed ------------------------------------------------------------------


def sign_in_message(
    challenge_id: uuid.UUID, challenge: str, device_id: uuid.UUID
) -> bytes:
    """The sign-in text: the context, the challenge id, the challenge (as sent) and the device id."""
    lines = [CONTEXT_SIGN_IN, str(challenge_id), challenge, str(device_id)]
    return "\n".join(lines).encode()


def registration_message(
    challenge_id: uuid.UUID, challenge: str, device_id: uuid.UUID, user_id: uuid.UUID
) -> bytes:
    """The registration text: as sign-in, under its own context, and the account id."""
    lines = [
        CONTEXT_REGISTRATION,
        str(challenge_id),
        challenge,
        str(device_id),
        str(user_id),
    ]
    return "\n".join(lines).encode()


# --- keys and signatures ----------------------------------------------------------------------


def parse_public_key(raw: bytes) -> bytes:
    """The key as SPKI DER, or ``400 public_key_invalid``.

    SPKI is the agreed form. The raw 65-byte uncompressed point that the iOS Secure Enclave exports is also
    read, so the app needs no wrapper. Only a point on the P-256 curve is a key.
    """
    try:
        if len(raw) == UNCOMPRESSED_POINT_LENGTH and raw[0] == 4:
            key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        else:
            loaded = serialization.load_der_public_key(raw)
            if not isinstance(loaded, ec.EllipticCurvePublicKey):
                raise ValueError("not an EC key")
            key = loaded
        if not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("not P-256")
    except ValueError, UnsupportedAlgorithm:
        raise services.field_error(
            "public_key",
            ErrorCode.PUBLIC_KEY_INVALID,
            "The key is not a P-256 public key.",
        ) from None
    return bytes(
        key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


def verify_signature(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """ECDSA P-256 with SHA-256, DER signature; ``False`` for anything that does not verify."""
    try:
        key = serialization.load_der_public_key(public_key)
        if not isinstance(key, ec.EllipticCurvePublicKey):
            return False
        key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature, ValueError, UnsupportedAlgorithm:
        return False
    return True


@functools.cache
def _dummy_public_key() -> bytes:
    """A key nobody holds: an unknown device is checked against it, so it costs the same as a known one."""
    key = ec.generate_private_key(ec.SECP256R1()).public_key()
    return bytes(
        key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


# --- the challenge ----------------------------------------------------------------------------


def _challenge_key(challenge_id: uuid.UUID) -> str:
    return f"devicekey:challenge:{challenge_id}"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def challenge_bytes(challenge: str) -> bytes:
    """The raw bytes of a challenge text: what an Android key attestation carries."""
    return base64.urlsafe_b64decode(challenge + "=" * (-len(challenge) % 4))


@limits.fail_closed
def issue_challenge(device_id: uuid.UUID) -> IssuedChallenge:
    """A new challenge for a device. The answer has the same shape whether the device is known or not."""
    challenge_id = uuid.uuid4()
    challenge = _b64url(secrets.token_bytes(CHALLENGE_BYTES))
    lifetime = settings.DEVICE_KEY_CHALLENGE_LIFETIME_SECONDS
    cache.set(
        _challenge_key(challenge_id),
        {"challenge": challenge, "device_id": str(device_id)},
        timeout=lifetime,
    )
    return IssuedChallenge(challenge_id, challenge, lifetime)


@limits.fail_closed
def _claim_challenge(challenge_id: uuid.UUID) -> _Challenge | None:
    """Take a challenge out of the cache. Only the request whose delete succeeds gets it."""
    key = _challenge_key(challenge_id)
    value = cache.get(key)
    if value is None or not cache.delete(key):
        return None
    return _Challenge(value["challenge"], uuid.UUID(value["device_id"]))


# --- registration -----------------------------------------------------------------------------


def _challenge_invalid() -> ValidationError:
    return services.field_error(
        "challenge_id",
        ErrorCode.CHALLENGE_INVALID,
        "The challenge is not valid. Ask for a new one.",
    )


def _attest(platform: str, chain: list[str] | None, challenge: str, key: bytes) -> bool:
    """Whether the key is attested. An Android key must be, unless this deployment may skip it."""
    if platform != Platform.ANDROID or not settings.DEVICE_KEY_REQUIRE_ATTESTATION:
        return False
    try:
        attestation.verify_android_key(
            chain=chain or [], challenge=challenge_bytes(challenge), public_key=key
        )
    except attestation.AttestationFailed as error:
        logger.warning("An Android key attestation was refused (rule %s).", error.rule)
        raise services.field_error(
            "attestation_chain",
            ErrorCode.ATTESTATION_INVALID,
            "The attestation was not accepted.",
        ) from None
    return True


def register_key(
    *,
    user: User,
    session: Session,
    current_password: str,
    public_key: bytes,
    challenge_id: uuid.UUID,
    signature: bytes,
    chain: list[str] | None,
) -> Registered:
    """Register the public key of the session's device, or replace the one it had (ADR 0018).

    In this order: a ``web`` session is refused; the current password is asked for again, with the lock
    it shares with the password change; the challenge is spent and must be the one asked for this device;
    the key must be a P-256 key; the signature must prove the phone holds its private half; an Android key
    must be attested. A key that was refused still spent its challenge, so the app starts again.
    """
    if session.platform == Platform.WEB:
        raise ValidationError(
            [
                ErrorDetail(
                    "Fingerprint sign-in is not available on the web.",
                    code=ErrorCode.PLATFORM_NOT_SUPPORTED.value,
                )
            ]
        )
    services.confirm_password(user, current_password, "current_password")
    record = _claim_challenge(challenge_id)
    if record is None or record.device_id != session.device_id:
        raise _challenge_invalid()
    spki = parse_public_key(public_key)
    message = registration_message(
        challenge_id, record.challenge, session.device_id, user.id
    )
    if not verify_signature(spki, message, signature):
        raise _challenge_invalid()
    attested = _attest(session.platform, chain, record.challenge, spki)
    now = timezone.now()
    with transaction.atomic():
        User.objects.select_for_update().get(
            pk=user.pk
        )  # one registration at a time per account
        existing = (
            DeviceKey.objects.select_for_update()
            .filter(device_id=session.device_id)
            .first()
        )
        others = (
            DeviceKey.objects.filter(user=user)
            .exclude(device_id=session.device_id)
            .count()
        )
        if others >= settings.DEVICE_KEY_MAX_PER_USER:
            raise conflict(ErrorCode.DEVICE_KEY_LIMIT_REACHED)
        key = existing or DeviceKey(device_id=session.device_id)
        created = existing is None or existing.user_id != user.pk
        key.user = user
        key.public_key = spki
        key.algorithm = "ES256"
        key.platform = session.platform
        key.device_name = session.device_name
        key.attested = attested
        key.created_at = now
        key.last_used_at = None
        key.expires_at = now + timedelta(days=settings.DEVICE_KEY_LIFETIME_DAYS)
        key.save()
    emails.send_device_key_added_notice(
        email=user.email, language=user.language, device_name=key.device_name
    )
    return Registered(key=key, created=created)


# --- sign-in ----------------------------------------------------------------------------------


def _usable(
    key: DeviceKey | None,
    record: _Challenge | None,
    valid: bool,
    device: services.DeviceInfo,
    now: datetime,
) -> bool:
    """Every condition of a sign-in, judged together so that no single one is told apart."""
    return (
        record is not None
        and record.device_id == device.device_id
        and key is not None
        and valid
        and key.expires_at > now
        and key.platform == device.platform
        and key.user.is_active
        and key.user.email_verified_at is not None
    )


def sign_in(
    *,
    challenge_id: uuid.UUID,
    signature: bytes,
    device: services.DeviceInfo,
    client: services.ClientInfo,
    language: str | None = None,
    time_zone: str | None = None,
) -> services.SignedIn:
    """Sign a phone in with its key and start a session (ADR 0018).

    The lock comes first. Then the challenge is spent, and the signature is always checked, against a key
    nobody holds when the device is unknown, so the time and the answer do not tell the failures apart:
    every one is ``401 device_sign_in_failed`` and counts against the device and the client address.
    """
    now = timezone.now()
    client_ip = rate_limit_key(client.ip)
    limits.check_device_sign_in_lock(device.device_id, client_ip, now)
    record = _claim_challenge(challenge_id)
    with transaction.atomic():
        key = (
            DeviceKey.objects.select_for_update()
            .select_related("user")
            .filter(device_id=device.device_id)
            .first()
        )
        message = sign_in_message(
            challenge_id,
            record.challenge if record is not None else "",
            device.device_id,
        )
        valid = verify_signature(
            bytes(key.public_key) if key is not None else _dummy_public_key(),
            message,
            signature,
        )
        if key is not None and _usable(key, record, valid, device, now):
            key.last_used_at = now
            key.expires_at = now + timedelta(days=settings.DEVICE_KEY_LIFETIME_DAYS)
            key.save(update_fields=["last_used_at", "expires_at"])
            limits.clear_device_sign_in_failures(device.device_id)
            services.remember_client(key.user, language, time_zone)
            return services.start_session(key.user, device, client, now)
    limits.record_device_sign_in_failure(device.device_id, client_ip, now)
    raise services.auth_failure(
        ErrorCode.DEVICE_SIGN_IN_FAILED, "The sign-in with the fingerprint failed."
    )


# --- the list and the removal -----------------------------------------------------------------


def keys_of(user: User) -> QuerySet[DeviceKey]:
    """The person's own keys, newest first."""
    return DeviceKey.objects.filter(user=user).order_by("-created_at", "-id")


def remove_key(*, user: User, device_id: uuid.UUID) -> None:
    """Delete the person's key of a device. Nothing there, or a key that is not theirs, is not an error."""
    DeviceKey.objects.filter(user=user, device_id=device_id).delete()
