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
from collections.abc import Callable
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
from config.exceptions import (
    CodeInvalid,
    FieldAuthenticationFailed,
    conflict,
    throttled,
)

from . import challenge, emails, events, limits, signals, tokens
from .models import (
    DeviceKey,
    EmailCode,
    EmailCodePurpose,
    EndReason,
    Language,
    PasswordResetToken,
    SecurityEventType,
    Session,
    SignInMethod,
    User,
)
from .network import rate_limit_key, truncate_ip
from .validators import password_errors

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
class ResetToken:
    reset_token: str
    expires_in: int


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class SignedIn:
    user: User
    tokens: TokenPair


def auth_failure(code: ErrorCode, detail: str) -> AuthenticationFailed:
    return AuthenticationFailed(detail=detail, code=code.value)


def normalize_email(email: str) -> str:
    return User.objects.normalize_email(email)


# --- the email codes --------------------------------------------------------------------------


# The label mixed into the keyed hash of a code, so a code of one purpose never matches another's even
# when the digits are the same. It is a fixed label of its own, not the purpose's name: the hash then
# depends on nothing but the label, the user and the code. A new purpose must be added here (a test
# checks), and a label must never change once codes exist, or the codes in flight stop working.
_CODE_DOMAINS = {
    EmailCodePurpose.VERIFY_EMAIL: "verify_email",
    EmailCodePurpose.PASSWORD_RESET: "reset",
    EmailCodePurpose.EMAIL_CHANGE: "email_change",
}


def _hash_code(user_id: uuid.UUID, purpose: str, code: str) -> str:
    """A keyed hash: a leaked table alone does not reveal a 6-digit code.

    HMAC-SHA256 keyed with the server's secret is the right tool for a short, expiring, attempt-limited
    code (it is not a password: those are hashed with Argon2id, ADR 0001).
    """
    domain = _CODE_DOMAINS[EmailCodePurpose(purpose)]
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"{domain}:{user_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _issue_code(
    user: User,
    now: datetime,
    purpose: str = EmailCodePurpose.VERIFY_EMAIL,
    payload: str = "",
) -> str:
    """Make a new code of this purpose for the user; it replaces every earlier one."""
    length = settings.EMAIL_CODE_LENGTH
    code = f"{secrets.randbelow(10**length):0{length}d}"
    EmailCode.objects.filter(user=user, purpose=purpose).delete()
    EmailCode.objects.create(
        user=user,
        purpose=purpose,
        code_hash=_hash_code(user.id, purpose, code),
        payload=payload,
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


def _redeem_code[T](
    *,
    user: User | None,
    purpose: str,
    code: str,
    email: str,
    now: datetime,
    accept: Callable[[User, EmailCode], T],
    signed_in: bool = False,
) -> T:
    """Check a 6-digit code and, when it is right, spend it and return what ``accept`` makes of the user.

    ``accept`` runs inside the same transaction as the spending of the code, so a code is never spent
    without its effect. A wrong code costs one of its tries, the last one kills it (``429``). A user that
    is ``None``, and a user with no code of this purpose (never asked for, or already spent), are answered
    like an address with nothing to verify (:func:`_reject_unknown_code`): the state of such an address
    can be set by anyone (a registration attempt counts as a send), so answering from the database for
    one and from the cache for the other would tell a real account from an unknown address.
    """
    maximum = settings.EMAIL_CODE_MAX_ATTEMPTS
    if user is None:
        _reject_unknown_code(email, now)

    outcome = "wrong"
    attempts_left = 0
    accepted: list[T] = []
    with transaction.atomic():
        record = (
            EmailCode.objects.select_for_update()
            .filter(user=user, purpose=purpose)
            .order_by("-created_at")
            .first()
        )
        if record is None:
            outcome = "none"
        elif record.expires_at <= now:
            outcome = "expired"
        elif record.attempts >= maximum:
            outcome = "exceeded"
        elif hmac.compare_digest(_hash_code(user.id, purpose, code), record.code_hash):
            record.delete()
            accepted.append(accept(user, record))
        else:
            EmailCode.objects.filter(pk=record.pk).update(attempts=F("attempts") + 1)
            attempts_left = max(0, maximum - (record.attempts + 1))

    if accepted:
        return accepted[0]
    if outcome == "none":
        if signed_in:
            raise _expired_code()  # the caller is signed in, so there is no one to hide
        # No live code of this purpose: answered like an address that has none, from the same state.
        _reject_unknown_code(email, now)
    if outcome == "expired":
        raise _expired_code()
    if outcome == "exceeded":
        raise throttled(
            ErrorCode.CODE_ATTEMPTS_EXCEEDED,
            limits.resend_wait(email, now),
            "Too many wrong codes.",
        )
    raise CodeInvalid(attempts_left=attempts_left)


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
    user = User.objects.filter(
        email=email, email_verified_at__isnull=True, is_active=True
    ).first()

    def accept(user: User, record: EmailCode) -> SignedIn:
        user.email_verified_at = now
        remember_client(user, language, time_zone)
        # The first accounts of a new person are made by the apps that depend on this one (ADR 0005).
        signals.email_verified.send(sender=User, user=user)
        return start_session(user, device, client, now, method=SignInMethod.EMAIL_CODE)

    return _redeem_code(
        user=user,
        purpose=EmailCodePurpose.VERIFY_EMAIL,
        code=code,
        email=email,
        now=now,
        accept=accept,
    )


# --- sessions and tokens ----------------------------------------------------------------------


def remember_client(user: User, language: str | None, time_zone: str | None) -> None:
    """The device the user signs in on decides the language and zone (docs/api-fields.md)."""
    if language:
        user.language = Language(language)
    if time_zone:
        user.time_zone = time_zone


def start_session(
    user: User,
    device: DeviceInfo,
    client: ClientInfo,
    now: datetime,
    *,
    method: SignInMethod,
) -> SignedIn:
    """Start a session and record the ``signed_in`` event with how the person signed in (ADR 0022)."""
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
    events.record_session_event(
        user, SecurityEventType.SIGNED_IN, session, sign_in_method=method, now=now
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
        raise auth_failure(
            ErrorCode.INVALID_CREDENTIALS, "The email or password is wrong."
        )
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
    remember_client(user, language, time_zone)
    with transaction.atomic():
        return start_session(user, device, client, now, method=SignInMethod.PASSWORD)


def end_session(
    session: Session, reason: EndReason, now: datetime | None = None
) -> bool:
    """End a session (once; a second call changes nothing). Returns whether this call ended it."""
    ended = Session.objects.filter(pk=session.pk, ended_at__isnull=True).update(
        ended_at=now or timezone.now(), end_reason=reason
    )
    return ended > 0


def sign_out(session: Session) -> None:
    """The person signs out: end the session and record ``signed_out`` (only when this call ended it)."""
    with transaction.atomic():
        now = timezone.now()
        if end_session(session, EndReason.SIGNED_OUT, now):
            events.record_session_event(
                session.user, SecurityEventType.SIGNED_OUT, session, now=now
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
        raise auth_failure(
            ErrorCode.SESSION_EXPIRED, "The session has expired."
        ) from None
    except tokens.TokenInvalid:
        raise auth_failure(ErrorCode.TOKEN_INVALID, "The token is not valid.") from None

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
        raise auth_failure(
            failure or ErrorCode.TOKEN_INVALID, "The session cannot be refreshed."
        )
    return TokenPair(
        access_token=tokens.issue_access_token(session, now),
        refresh_token=successor,
        expires_in=settings.ACCESS_TOKEN_LIFETIME_SECONDS,
    )


def _advance_refresh_token(session: Session, now: datetime) -> str:
    """Retire the session's refresh token and make its successor; the caller saves the session."""
    session.generation += 1
    session.rotated_at = now
    successor = tokens.issue_refresh_token(session)
    session.refresh_hash = tokens.hash_token(successor)
    return successor


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
        return None, _advance_refresh_token(session, now)
    if generation == session.generation - 1 and now - session.rotated_at <= window:
        return None, tokens.issue_refresh_token(session)
    if end_session(session, EndReason.TOKEN_REUSE, now):
        events.record_session_event(
            session.user, SecurityEventType.TOKEN_REUSE_DETECTED, session, now=now
        )
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
        raise auth_failure(
            ErrorCode.TOKEN_EXPIRED, "The access token has expired."
        ) from None
    except tokens.TokenInvalid:
        raise auth_failure(ErrorCode.TOKEN_INVALID, "The token is not valid.") from None
    session = (
        Session.objects.select_related("user")
        .filter(pk=claims.session_id, user_id=claims.user_id)
        .first()
    )
    if session is None:
        raise auth_failure(ErrorCode.TOKEN_INVALID, "The token is not valid.")
    failure = _usable_or_code(session, now)
    if failure is not None:
        raise auth_failure(failure, "The session is no longer valid.")
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
        raise auth_failure(ErrorCode.TOKEN_INVALID, "The token is not valid.") from None
    with transaction.atomic():
        session = (
            Session.objects.select_for_update()
            .select_related("user")
            .filter(pk=claims.session_id, user_id=claims.user_id, ended_at__isnull=True)
            .first()
        )
        if session is not None and end_session(session, EndReason.SIGNED_OUT, now):
            events.record_session_event(
                session.user, SecurityEventType.SIGNED_OUT, session, now=now
            )


# --- password reset, password change, account deletion (ADR 0014) -------------------------------


def request_password_reset(*, email: str) -> CodeSent:
    """Email a reset code to a verified, active account. The answer is the same for every address.

    Nothing about the answer tells whether the address has an account (OWASP): the limits are keyed by
    the address whether or not it exists, and an unknown, a pending or a deactivated address is answered
    exactly like a real one. A pending (unverified) account gets no reset code; it verifies with its
    registration code instead. The reset code draws on the same hourly budget as every other code.
    """
    email = normalize_email(email)
    limits.reserve_code_send(email)
    user = User.objects.filter(
        email=email, email_verified_at__isnull=False, is_active=True
    ).first()
    if user is not None:
        with transaction.atomic():
            code = _issue_code(user, timezone.now(), EmailCodePurpose.PASSWORD_RESET)
            PasswordResetToken.objects.filter(user=user).delete()
        emails.send_password_reset_code(
            email=user.email,
            language=user.language,
            code=code,
            lifetime_seconds=settings.EMAIL_CODE_LIFETIME_SECONDS,
        )
    return _code_sent(email)


def verify_password_reset(*, email: str, code: str) -> ResetToken:
    """Accept the reset code and hand out a single-use reset token. It does not sign anyone in.

    Wrong, expired and dead codes are answered as for the email verification, and an address with no
    reset to verify is answered like a real one (:func:`_reject_unknown_code`).
    """
    email = normalize_email(email)
    now = timezone.now()
    user = User.objects.filter(
        email=email, email_verified_at__isnull=False, is_active=True
    ).first()

    def accept(user: User, record: EmailCode) -> ResetToken:
        token = secrets.token_urlsafe(32)
        lifetime = settings.PASSWORD_RESET_TOKEN_LIFETIME_SECONDS
        PasswordResetToken.objects.update_or_create(
            user=user,
            defaults={
                "token_hash": tokens.hash_token(token),
                "created_at": now,
                "expires_at": now + timedelta(seconds=lifetime),
            },
        )
        return ResetToken(reset_token=token, expires_in=lifetime)

    return _redeem_code(
        user=user,
        purpose=EmailCodePurpose.PASSWORD_RESET,
        code=code,
        email=email,
        now=now,
        accept=accept,
    )


def field_error(field: str, code: ErrorCode, detail: str) -> ValidationError:
    return ValidationError({field: [ErrorDetail(detail, code=code.value)]})


def _forget_password_secrets(user: User) -> None:
    """A new password makes every outstanding reset code and token useless."""
    EmailCode.objects.filter(
        user=user, purpose=EmailCodePurpose.PASSWORD_RESET
    ).delete()
    PasswordResetToken.objects.filter(user=user).delete()


def reset_password(*, reset_token: str, password: str) -> None:
    """Set a new password with the token from :func:`verify_password_reset`.

    The token works once. The password rules apply, but "same as the current password" does not: the
    reset proves the mailbox, not the old password, and refusing a repeat would let whoever reads the
    mailbox test guesses of the current password here without any lock. A failed password rule keeps the
    token, so the user can try another password until it expires. Every session of the user ends.
    """
    now = timezone.now()
    with transaction.atomic():
        record = (
            PasswordResetToken.objects.select_for_update()
            .select_related("user")
            .filter(token_hash=tokens.hash_token(reset_token))
            .first()
        )
        if (
            record is None
            or record.expires_at <= now
            or not record.user.is_active
            or record.user.email_verified_at is None
        ):
            raise field_error(
                "reset_token",
                ErrorCode.RESET_TOKEN_INVALID,
                "The reset token is not valid. Ask for a new code.",
            )
        user = record.user
        errors = password_errors(password, user)
        if errors:
            raise ValidationError({"password": errors})
        user.set_password(password)
        user.save(update_fields=["password"])
        _end_sessions(user, now)
        _forget_password_secrets(user)
        forget_device_keys(user, now=now)
        events.record_account_event(user, SecurityEventType.PASSWORD_RESET, now=now)
    emails.send_password_changed_notice(email=user.email, language=user.language)


def forget_device_keys(
    user: User, *, acting: Session | None = None, now: datetime | None = None
) -> None:
    """Delete every device key of the user (ADR 0018), recording ``device_key_removed`` for each (ADR 0022).

    A password change or reset and an email change are the person's answer to "my account may be
    compromised", so a phone that could sign in without the password loses that power with them.
    Ending a session never does this: signing out must leave the key, which is the point of it.
    ``acting`` is the session that did the change, if there is one (a reset has none).
    """
    keys = list(DeviceKey.objects.filter(user=user))
    for key in keys:
        events.record_key_event(
            user, SecurityEventType.DEVICE_KEY_REMOVED, key, acting=acting, now=now
        )
    DeviceKey.objects.filter(pk__in=[key.pk for key in keys]).delete()


def _end_sessions(user: User, now: datetime, keep: Session | None = None) -> None:
    """End the user's live sessions with ``password_changed``, except ``keep``."""
    live = Session.objects.filter(user=user, ended_at__isnull=True)
    if keep is not None:
        live = live.exclude(pk=keep.pk)
    live.update(ended_at=now, end_reason=EndReason.PASSWORD_CHANGED)


def confirm_password(user: User, password: str, field: str) -> None:
    """Ask for the current password again (change password, delete account), with a lock on wrong answers.

    The count is per account and does not depend on the client address, so a stolen access token cannot
    be used to guess the password here. A wrong answer is ``400 invalid_password`` on ``field``; the
    answer that reaches the limit is ``429 password_attempts_exceeded``.
    """
    limits.check_password_lock(user.id)
    if not user.check_password(password):
        limits.record_password_failure(user.id)
        raise field_error(
            field, ErrorCode.INVALID_PASSWORD, "The password is not correct."
        )
    limits.clear_password_failures(user.id)


def change_password(
    *, user: User, session: Session, current_password: str, password: str
) -> TokenPair:
    """Change the password of a signed-in user (ADR 0014).

    The current password is asked for again. The new one must differ from it (``password_unchanged``,
    checked before the other rules, because the current password may predate them) and pass the password
    rules. Every other session ends; this one continues with a new token pair, so the old refresh token
    is retired. The owner gets an email, so a change by a stranger is noticed.
    """
    confirm_password(user, current_password, "current_password")
    if hmac.compare_digest(password.encode(), current_password.encode()):
        raise field_error(
            "password",
            ErrorCode.PASSWORD_UNCHANGED,
            "The new password must be different from the current one.",
        )
    errors = password_errors(password, user)
    if errors:
        raise ValidationError({"password": errors})
    now = timezone.now()
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        current = (
            Session.objects.select_for_update()
            .select_related("user")
            .filter(pk=session.pk, user_id=user.pk)
            .first()
        )
        if current is None:
            raise auth_failure(ErrorCode.TOKEN_INVALID, "The token is not valid.")
        failure = _usable_or_code(current, now)
        if failure is not None:
            raise auth_failure(failure, "The session is no longer valid.")
        locked.set_password(password)
        locked.save(update_fields=["password"])
        _end_sessions(locked, now, keep=current)
        _forget_password_secrets(locked)
        forget_device_keys(locked, acting=current, now=now)
        events.record_session_event(
            locked, SecurityEventType.PASSWORD_CHANGED, current, now=now
        )
        refresh_token = _advance_refresh_token(current, now)
        current.save()
    emails.send_password_changed_notice(email=user.email, language=user.language)
    return TokenPair(
        access_token=tokens.issue_access_token(current, now),
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_LIFETIME_SECONDS,
    )


def delete_account(*, user: User, password: str) -> None:
    """Delete the account and everything it owns, in one transaction, after the password is confirmed.

    Every model that holds user data points at the user with ``on_delete=CASCADE`` (ADR 0001), so the
    one delete removes the sessions, the codes and, in later steps, all the financial data. Nothing is
    kept: there is no soft delete and no anonymised copy. The address is used once more, for the
    confirmation email, and then forgotten.
    """
    confirm_password(user, password, "password")
    email, language = user.email, user.language
    with transaction.atomic():
        User.objects.filter(pk=user.pk).delete()
    emails.send_account_deleted_notice(email=email, language=language)


# --- the name and the email of a signed-in user (ADR 0015) ---------------------------------------


def change_name(*, user: User, name: str) -> User:
    """Set the display name; the model trims it."""
    user.name = name
    user.save(update_fields=["name"])
    return user


def request_email_change(*, user: User, email: str, current_password: str) -> CodeSent:
    """Start a change of the sign-in address: email a code to the **new** address.

    The current password is asked for again, with the same lock as the other confirmations. The old
    address stays in force until the code is accepted. Every attempt counts against the account's
    hourly cap before anything else is judged, so the call cannot be used to send mail to many strangers
    or to probe many addresses. The caller is signed in and has proved the password, so a taken address
    is told as ``409 email_already_registered``; that is the one place this API says so. A new request
    replaces the earlier pending change.
    """
    new_email = normalize_email(email)
    confirm_password(user, current_password, "current_password")
    limits.reserve_email_change(user.id)
    if new_email == user.email:
        raise field_error(
            "email",
            ErrorCode.EMAIL_UNCHANGED,
            "This is already the email of the account.",
        )
    if User.objects.filter(email=new_email).exists():
        raise conflict(ErrorCode.EMAIL_ALREADY_REGISTERED)
    limits.reserve_code_send(new_email)
    with transaction.atomic():
        code = _issue_code(
            user, timezone.now(), EmailCodePurpose.EMAIL_CHANGE, payload=new_email
        )
    emails.send_email_change_code(
        email=new_email,
        language=user.language,
        code=code,
        lifetime_seconds=settings.EMAIL_CODE_LIFETIME_SECONDS,
    )
    return _code_sent(new_email)


def confirm_email_change(*, user: User, session: Session, code: str) -> User:
    """Accept the code: the address changes, every other session ends and the old address is told.

    The code is spent with the change in one transaction, so a refused change (the address was taken
    meanwhile: ``409``) keeps the code. The new address counts as verified, because the code proved it.
    Outstanding password reset codes were mailed to the old address, so they are deleted too.
    """
    now = timezone.now()
    old_email = user.email

    def accept(user: User, record: EmailCode) -> User:
        new_email = record.payload
        if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
            raise conflict(ErrorCode.EMAIL_ALREADY_REGISTERED)
        user.email = new_email
        user.email_verified_at = now
        try:
            with transaction.atomic():
                user.save(update_fields=["email", "email_verified_at"])
        except IntegrityError:
            raise conflict(ErrorCode.EMAIL_ALREADY_REGISTERED) from None
        live = Session.objects.filter(user=user, ended_at__isnull=True)
        live.exclude(pk=session.pk).update(
            ended_at=now, end_reason=EndReason.EMAIL_CHANGED
        )
        _forget_password_secrets(user)
        forget_device_keys(user, acting=session, now=now)
        events.record_session_event(
            user, SecurityEventType.EMAIL_CHANGED, session, now=now
        )
        return user

    # A dead code is replaced by asking again, which the new address's send budget paces.
    pending = (
        EmailCode.objects.filter(user=user, purpose=EmailCodePurpose.EMAIL_CHANGE)
        .order_by("-created_at")
        .first()
    )
    changed = _redeem_code(
        user=user,
        purpose=EmailCodePurpose.EMAIL_CHANGE,
        code=code,
        email=pending.payload if pending is not None else old_email,
        now=now,
        accept=accept,
        signed_in=True,
    )
    emails.send_email_changed_notice(email=old_email, language=changed.language)
    return changed


def cancel_email_change(*, user: User) -> None:
    """Drop the pending change. Cancelling when there is none is not an error."""
    EmailCode.objects.filter(user=user, purpose=EmailCodePurpose.EMAIL_CHANGE).delete()
