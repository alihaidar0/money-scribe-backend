"""The authentication rules (ADR 0001, 0006, 0009), free of HTTP: the views only translate.

Wherever a rule must change the database *and* answer with an error (a wrong code costs an attempt, a
reused refresh token ends its session), the change is committed first and the error is raised after the
transaction block, because an exception inside it would roll the change back.
"""

import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from typing import NoReturn

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import (
    AuthenticationFailed,
    ErrorDetail,
    PermissionDenied,
    ValidationError,
)

from config.error_codes import ErrorCode
from config.exceptions import CodeInvalid, FieldAuthenticationFailed, throttled

from . import challenge, emails, limits, tokens
from .models import (
    EmailCode,
    EmailCodePurpose,
    EndReason,
    Language,
    Session,
    User,
)
from .network import rate_limit_key, truncate_ip

logger = logging.getLogger(__name__)

# The session code the client sees for each reason a session ended (ADR 0004). An account deletion
# removes the session row with the account, so it never shows up as an ended session.
_END_REASON_CODES = {
    EndReason.SIGNED_OUT: ErrorCode.SESSION_SIGNED_OUT,
    EndReason.REVOKED_BY_USER: ErrorCode.SESSION_REVOKED_BY_USER,
    EndReason.PASSWORD_CHANGED: ErrorCode.SESSION_PASSWORD_CHANGED,
    EndReason.EMAIL_CHANGED: ErrorCode.SESSION_EMAIL_CHANGED,
    EndReason.TOKEN_REUSE: ErrorCode.SESSION_TOKEN_REUSE,
    EndReason.EXPIRED: ErrorCode.SESSION_EXPIRED,
    EndReason.ACCOUNT_DELETED: ErrorCode.TOKEN_INVALID,
}


@dataclass(frozen=True)
class DeviceInfo:
    device_id: uuid.UUID
    device_name: str
    platform: str
    app_version: str
    os_version: str = ""


@dataclass(frozen=True)
class ClientInfo:
    """What the server learns from the request itself, not from the body."""

    ip: str | None = None
    user_agent: str = ""


@dataclass(frozen=True)
class CodeSent:
    email: str
    code_expires_in: int
    resend_available_in: int


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class SignedIn:
    user: User
    tokens: TokenPair


def _failure(code: ErrorCode, detail: str) -> AuthenticationFailed:
    return AuthenticationFailed(detail=detail, code=code.value)


def normalize_email(email: str) -> str:
    return User.objects.normalize_email(email)


# --- the email codes --------------------------------------------------------------------------


def _hash_code(user_id: uuid.UUID, purpose: str, code: str) -> str:
    """A keyed hash: a leaked table alone does not reveal a 6-digit code."""
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"{purpose}:{user_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _issue_code(user: User, now: datetime) -> str:
    """Make a new verification code for the user; it replaces every earlier one."""
    length = settings.EMAIL_CODE_LENGTH
    code = f"{secrets.randbelow(10**length):0{length}d}"
    purpose = EmailCodePurpose.VERIFY_EMAIL
    EmailCode.objects.filter(user=user, purpose=purpose).delete()
    EmailCode.objects.create(
        user=user,
        purpose=purpose,
        code_hash=_hash_code(user.id, purpose, code),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.EMAIL_CODE_LIFETIME_SECONDS),
    )
    return code


def _code_sent(email: str) -> CodeSent:
    return CodeSent(
        email=email,
        code_expires_in=settings.EMAIL_CODE_LIFETIME_SECONDS,
        resend_available_in=settings.EMAIL_CODE_RESEND_COOLDOWN_SECONDS,
    )


def _mail_code(user: User, code: str) -> None:
    emails.send_verification_code(
        email=user.email,
        language=user.language,
        code=code,
        lifetime_seconds=settings.EMAIL_CODE_LIFETIME_SECONDS,
    )


def register(
    *, name: str, email: str, password: str, language: str, time_zone: str
) -> CodeSent:
    """Create an unverified account, or replace the pending one, and email a code.

    The answer is the same whether the address is free, pending or already verified, so registration
    cannot be used to find out who has an account (OWASP). A pending account is registered again: the
    new name, password, language and zone replace the old ones, so the real owner's registration wins
    over an earlier attempt (ADR 0001). For a verified address nothing changes; its owner gets an
    email that says someone tried. That path hashes the password too, so it takes as long as the others.
    """
    email = normalize_email(email)
    limits.reserve_code_send(email)
    now = timezone.now()
    existing = User.objects.filter(email=email).first()
    if existing is not None and existing.email_verified_at is not None:
        make_password(password)
        emails.send_account_exists_notice(
            email=existing.email, language=existing.language
        )
        return _code_sent(email)
    try:
        with transaction.atomic():
            if existing is None:
                user = User.objects.create_user(
                    email, name, password, language=language, time_zone=time_zone
                )
            else:
                user = User.objects.select_for_update().get(pk=existing.pk)
                user.name = name
                user.language = language
                user.time_zone = time_zone
                user.set_password(password)
                user.save()
            code = _issue_code(user, now)
    except IntegrityError:
        # Someone registered the same address at the same moment; that request sends the code.
        return _code_sent(email)
    _mail_code(user, code)
    return _code_sent(email)


def resend_code(*, email: str) -> CodeSent:
    """Send a new code. The answer is the same for every address, so it reveals nothing."""
    email = normalize_email(email)
    limits.reserve_code_send(email)
    user = User.objects.filter(
        email=email, email_verified_at__isnull=True, is_active=True
    ).first()
    if user is not None:
        with transaction.atomic():
            code = _issue_code(user, timezone.now())
        _mail_code(user, code)
    return _code_sent(email)


def _expired_code() -> ValidationError:
    return ValidationError(
        {
            "code": [
                ErrorDetail("The code has expired.", code=ErrorCode.CODE_EXPIRED.value)
            ]
        }
    )


def _reject_unknown_code(email: str, now: datetime) -> NoReturn:
    """Answer a code for an address with nothing to verify exactly like a real pending account.

    The answers are the same in every state: an expired code, a wrong code with ``attempts_left``
    counting down, then ``429`` once the tries are used, and a new code starts over
    (``limits.phantom_code_attempt``). Verifying therefore cannot tell who has registered.
    """
    maximum = settings.EMAIL_CODE_MAX_ATTEMPTS
    used = limits.phantom_code_attempt(email, now)
    if used is None:
        raise _expired_code()
    if used > maximum:
        raise throttled(
            ErrorCode.CODE_ATTEMPTS_EXCEEDED,
            limits.resend_wait(email, now),
            "Too many wrong codes.",
        )
    raise CodeInvalid(attempts_left=maximum - used)


def verify_email(
    *,
    email: str,
    code: str,
    device: DeviceInfo,
    client: ClientInfo,
    language: str | None = None,
    time_zone: str | None = None,
) -> SignedIn:
    """Accept the 6-digit code: the email is verified and the call signs the user in (ADR 0006)."""
    email = normalize_email(email)
    now = timezone.now()
    maximum = settings.EMAIL_CODE_MAX_ATTEMPTS
    purpose = EmailCodePurpose.VERIFY_EMAIL
    user = User.objects.filter(
        email=email, email_verified_at__isnull=True, is_active=True
    ).first()
    if user is None:
        _reject_unknown_code(email, now)

    outcome = "wrong"
    attempts_left = 0
    signed_in: SignedIn | None = None
    with transaction.atomic():
        record = (
            EmailCode.objects.select_for_update()
            .filter(user=user, purpose=purpose)
            .order_by("-created_at")
            .first()
        )
        if record is None or record.expires_at <= now:
            outcome = "expired"
        elif record.attempts >= maximum:
            outcome = "exceeded"
        elif hmac.compare_digest(_hash_code(user.id, purpose, code), record.code_hash):
            outcome = "ok"
            record.delete()
            user.email_verified_at = now
            _remember_client(user, language, time_zone)
            signed_in = _start_session(user, device, client, now)
        else:
            EmailCode.objects.filter(pk=record.pk).update(attempts=F("attempts") + 1)
            attempts_left = max(0, maximum - (record.attempts + 1))

    if signed_in is not None:
        return signed_in
    if outcome == "expired":
        raise _expired_code()
    if outcome == "exceeded":
        raise throttled(
            ErrorCode.CODE_ATTEMPTS_EXCEEDED,
            limits.resend_wait(email, now),
            "Too many wrong codes.",
        )
    raise CodeInvalid(attempts_left=attempts_left)


# --- sessions and tokens ----------------------------------------------------------------------


def _remember_client(user: User, language: str | None, time_zone: str | None) -> None:
    """The device the user signs in on decides the language and zone (docs/api-fields.md)."""
    if language:
        user.language = Language(language)
    if time_zone:
        user.time_zone = time_zone


def _start_session(
    user: User, device: DeviceInfo, client: ClientInfo, now: datetime
) -> SignedIn:
    session = Session(
        user=user,
        device_id=device.device_id,
        device_name=device.device_name,
        platform=device.platform,
        os_version=device.os_version,
        app_version=device.app_version,
        created_at=now,
        last_active_at=now,
        rotated_at=now,
        expires_at=now + timedelta(days=settings.SESSION_ABSOLUTE_LIFETIME_DAYS),
        ip=truncate_ip(client.ip),
        user_agent=client.user_agent[:255] if device.platform == "web" else "",
    )
    refresh_token = tokens.issue_refresh_token(session)
    session.refresh_hash = tokens.hash_token(refresh_token)
    session.save()
    user.last_login = now
    user.save(
        update_fields=["last_login", "email_verified_at", "language", "time_zone"]
    )
    return SignedIn(
        user=user,
        tokens=TokenPair(
            access_token=tokens.issue_access_token(session, now),
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_LIFETIME_SECONDS,
        ),
    )


@cache
def _dummy_hash() -> str:
    return make_password("not-a-real-password-0")


def _require_challenge(
    token: str, client: ClientInfo, client_ip: str, now: datetime
) -> None:
    """Ask a client address that keeps failing for a solved bot challenge, and check it (ADR 0013).

    No token is only asked for again and costs nothing; a token that does not verify counts as a
    failure of the address, so a bot cannot spin on it for free.
    """
    if not challenge.is_enabled() or not limits.challenge_required(client_ip):
        return
    if not token:
        raise challenge.required()
    if not challenge.verify(token, client.ip):
        limits.record_address_failure(client_ip, now)
        raise challenge.invalid()


def sign_in(
    *,
    email: str,
    password: str,
    device: DeviceInfo,
    client: ClientInfo,
    language: str | None = None,
    time_zone: str | None = None,
    captcha_token: str = "",
) -> SignedIn:
    """Check the credentials and start a session.

    The lock comes first, so while an account or a client address is locked even the right password
    answers ``429``. Then the email: no account is ``email_not_registered`` (``attr`` ``email``), whatever
    the password is, and only a registered email has its password checked (``wrong_password``, ``attr``
    ``password``). That is deliberate account enumeration (ADR 0012); the client address limit is its
    brake. An inactive account is ``invalid_credentials`` whatever the password, so it is not named.
    ``email_not_verified`` is only given after the right password. Every branch does one password
    hash, so the answers take the same time. A client address that has failed several sign-ins must
    first send a solved bot challenge (ADR 0013); that is judged before the email, so it tells nothing.
    """
    email = normalize_email(email)
    now = timezone.now()
    client_ip = rate_limit_key(client.ip)
    limits.check_sign_in_lock(email, client_ip, now)
    _require_challenge(captcha_token, client, client_ip, now)
    candidate = User.objects.filter(email=email).first()
    if candidate is None:
        check_password(password, _dummy_hash())  # the same work as a real check
        limits.record_address_failure(client_ip, now)
        raise FieldAuthenticationFailed(
            ErrorCode.EMAIL_NOT_REGISTERED, "email", "No account has this email."
        )
    password_is_right = candidate.check_password(password)
    if not candidate.is_active:
        limits.record_sign_in_failure(email, client_ip, now)
        raise _failure(ErrorCode.INVALID_CREDENTIALS, "The email or password is wrong.")
    if not password_is_right:
        limits.record_sign_in_failure(email, client_ip, now)
        raise FieldAuthenticationFailed(
            ErrorCode.WRONG_PASSWORD, "password", "The password is wrong."
        )
    user = candidate
    if user.email_verified_at is None:
        raise PermissionDenied(
            detail="The email is not verified yet.",
            code=ErrorCode.EMAIL_NOT_VERIFIED.value,
        )
    limits.clear_sign_in_failures(email, client_ip)
    _remember_client(user, language, time_zone)
    with transaction.atomic():
        return _start_session(user, device, client, now)


def end_session(
    session: Session, reason: EndReason, now: datetime | None = None
) -> None:
    """End a session (once; a second call changes nothing)."""
    Session.objects.filter(pk=session.pk, ended_at__isnull=True).update(
        ended_at=now or timezone.now(), end_reason=reason
    )


def _usable_or_code(session: Session, now: datetime) -> ErrorCode | None:
    """Why this session cannot be used, as the code the client sees; ``None`` when it can."""
    if session.ended_at is not None:
        return _END_REASON_CODES[EndReason(session.end_reason)]
    if not session.user.is_active:
        return ErrorCode.TOKEN_INVALID
    if session.expires_at <= now:
        return ErrorCode.SESSION_EXPIRED
    return None


def refresh(*, refresh_token: str, device: DeviceInfo, client: ClientInfo) -> TokenPair:
    """Trade a refresh token for a new pair (ADR 0006).

    The current token rotates. The token it replaced, presented again within the replay window, is a
    retry after a lost answer and gets the same successor. Any older token is a replay: the whole
    session ends.
    """
    now = timezone.now()
    try:
        claims = tokens.decode_refresh_token(refresh_token, now)
    except tokens.TokenExpired:
        _end_expired_session(refresh_token, now)
        raise _failure(ErrorCode.SESSION_EXPIRED, "The session has expired.") from None
    except tokens.TokenInvalid:
        raise _failure(ErrorCode.TOKEN_INVALID, "The token is not valid.") from None

    failure: ErrorCode | None = None
    successor = ""
    with transaction.atomic():
        session = (
            Session.objects.select_for_update()
            .select_related("user")
            .filter(pk=claims.session_id, user_id=claims.user_id)
            .first()
        )
        if session is None:
            failure = ErrorCode.TOKEN_INVALID
        else:
            failure = _usable_or_code(session, now)
            if failure is None:
                failure, successor = _rotate(
                    session, claims.generation, refresh_token, now
                )
            if failure is None:
                session.device_name = device.device_name
                session.app_version = device.app_version
                session.os_version = device.os_version
                session.last_active_at = now
                session.ip = truncate_ip(client.ip) or session.ip
                session.save()

    if failure is not None or session is None:
        raise _failure(
            failure or ErrorCode.TOKEN_INVALID, "The session cannot be refreshed."
        )
    return TokenPair(
        access_token=tokens.issue_access_token(session, now),
        refresh_token=successor,
        expires_in=settings.ACCESS_TOKEN_LIFETIME_SECONDS,
    )


def _rotate(
    session: Session, generation: int, refresh_token: str, now: datetime
) -> tuple[ErrorCode | None, str]:
    """Apply the rotation rules to a live session; returns the failure (or ``None``) and the successor.

    The caller saves the session. A replayed token ends the session here, inside the caller's
    transaction, which therefore must not roll back: it only raises after the block.
    """
    window = timedelta(seconds=settings.REFRESH_REPLAY_WINDOW_SECONDS)
    if generation == session.generation:
        if not hmac.compare_digest(
            tokens.hash_token(refresh_token), session.refresh_hash
        ):
            return ErrorCode.TOKEN_INVALID, ""
        session.generation += 1
        session.rotated_at = now
        successor = tokens.issue_refresh_token(session)
        session.refresh_hash = tokens.hash_token(successor)
        return None, successor
    if generation == session.generation - 1 and now - session.rotated_at <= window:
        return None, tokens.issue_refresh_token(session)
    end_session(session, EndReason.TOKEN_REUSE, now)
    return ErrorCode.SESSION_TOKEN_REUSE, ""


def _end_expired_session(refresh_token: str, now: datetime) -> None:
    """A refresh token past its life: mark its session expired (the signature is still checked)."""
    try:
        claims = tokens.decode_refresh_token(refresh_token, now, allow_expired=True)
    except tokens.TokenError:
        return
    Session.objects.filter(
        pk=claims.session_id, user_id=claims.user_id, ended_at__isnull=True
    ).update(ended_at=now, end_reason=EndReason.EXPIRED)


def authenticate_access_token(
    token: str, *, client_ip: str | None = None
) -> tuple[User, Session]:
    """The check on every request: a valid token **and** a live session, in one query (ADR 0006)."""
    now = timezone.now()
    try:
        claims = tokens.decode_access_token(token, now)
    except tokens.TokenExpired:
        raise _failure(
            ErrorCode.TOKEN_EXPIRED, "The access token has expired."
        ) from None
    except tokens.TokenInvalid:
        raise _failure(ErrorCode.TOKEN_INVALID, "The token is not valid.") from None
    session = (
        Session.objects.select_related("user")
        .filter(pk=claims.session_id, user_id=claims.user_id)
        .first()
    )
    if session is None:
        raise _failure(ErrorCode.TOKEN_INVALID, "The token is not valid.")
    failure = _usable_or_code(session, now)
    if failure is not None:
        raise _failure(failure, "The session is no longer valid.")
    _touch(session, now, client_ip)
    return session.user, session


def _touch(session: Session, now: datetime, client_ip: str | None) -> None:
    """Record activity at most once per interval, so a busy client does not write on every call."""
    interval = timedelta(seconds=settings.SESSION_ACTIVITY_INTERVAL_SECONDS)
    if now - session.last_active_at < interval:
        return
    Session.objects.filter(pk=session.pk, last_active_at__lte=now - interval).update(
        last_active_at=now, ip=truncate_ip(client_ip) or session.ip
    )


def sign_out_with_refresh_token(refresh_token: str) -> None:
    """End the session a refresh token belongs to, even if its access token has expired."""
    now = timezone.now()
    try:
        claims = tokens.decode_refresh_token(refresh_token, now, allow_expired=True)
    except tokens.TokenError:
        raise _failure(ErrorCode.TOKEN_INVALID, "The token is not valid.") from None
    Session.objects.filter(
        pk=claims.session_id, user_id=claims.user_id, ended_at__isnull=True
    ).update(ended_at=now, end_reason=EndReason.SIGNED_OUT)
