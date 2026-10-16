"""Counters and cooldowns kept in the cache, which is Redis in production (ADR 0006).

Three properties matter for a brake against guessing:

* **Atomic.** A count is raised with one ``incr`` and a cooldown is claimed with one ``add`` (``SET NX``),
  never with a read followed by a write, so concurrent requests cannot slip past a limit together.
  The windows are fixed windows, which allow a short burst at a window boundary.
* **Fail closed.** If the store is down, the request is refused with ``503`` and ``Retry-After``
  (:func:`fail_closed`). Letting it through would switch the brake off exactly when it is least
  watched. Calls that do not use a limit (a refresh, a request with an access token) are unaffected.
* **No oracle.** Limits are keyed by the normalized email address whether or not an account exists, so
  an unknown address behaves exactly like a known one.
"""

import contextlib
import functools
import hashlib
import logging
import math
from collections.abc import Callable
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from redis.exceptions import RedisError

from config.error_codes import ErrorCode
from config.exceptions import service_unavailable, throttled

logger = logging.getLogger(__name__)

HOUR = 3600


def fail_closed[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Turn a failure of the limit store into ``503`` instead of letting the request through."""

    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except RedisError:
            logger.error("The limit store is unavailable; the request is refused.")
            raise service_unavailable() from None

    return wrapper


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _increment(key: str, timeout: int) -> int:
    """Add one to a counter that expires ``timeout`` seconds after it was first created."""
    cache.add(key, 0, timeout=timeout)
    try:
        return int(cache.incr(key))
    except ValueError:  # the counter expired between the two calls
        cache.set(key, 1, timeout=timeout)
        return 1


def _seconds_until(moment: float, now: datetime) -> int:
    """Whole seconds to wait, never less than 1, so a ``Retry-After`` is always usable."""
    return max(1, math.ceil(moment - now.timestamp()))


@fail_closed
def hit(key: str, window: int, now: datetime | None = None) -> tuple[int, int]:
    """Count one request in a fixed window; returns the count so far and the seconds until the window ends.

    The counter and the window's start are separate keys. If the store still holds a counter that the
    clock says is over (or lost its start), the window begins again at one.
    """
    now = now or timezone.now()
    count = _increment(key, window)
    started = cache.get(f"{key}:start")
    if started is None or started + window <= now.timestamp():
        cache.set(key, 1, timeout=window)
        cache.set(f"{key}:start", now.timestamp(), timeout=window)
        count, started = 1, now.timestamp()
    return count, _seconds_until(started + window, now)


# --- the sign-in lock -------------------------------------------------------------------------


def _lock_keys(email: str, client_ip: str) -> tuple[str, str]:
    """The lock of this account for this client address, and the lock of the account for everyone."""
    account = _digest(email)
    return f"signin:lock:{account}:{_digest(client_ip)}", f"signin:lock:{account}"


@fail_closed
def check_sign_in_lock(email: str, client_ip: str, now: datetime | None = None) -> None:
    """Raise ``sign_in_locked`` (429) while this account is locked for this client or for everyone."""
    now = now or timezone.now()
    unlocks = [cache.get(key) for key in _lock_keys(email, client_ip)]
    active = [
        unlock for unlock in unlocks if unlock is not None and unlock > now.timestamp()
    ]
    if active:
        raise throttled(
            ErrorCode.SIGN_IN_LOCKED,
            _seconds_until(max(active), now),
            "Too many failed sign-in attempts.",
        )


@fail_closed
def record_sign_in_failure(
    email: str, client_ip: str, now: datetime | None = None
) -> None:
    """Count a failed sign-in, and raise 429 if it locks the account for this client or for everyone.

    Five failures from one address lock the pair of account and address. Fifty from all addresses
    together lock the account for everyone, which only a distributed guess can reach.
    """
    now = now or timezone.now()
    window = settings.SIGN_IN_LOCK_SECONDS
    account = _digest(email)
    pair_lock, account_lock = _lock_keys(email, client_ip)
    pair_key = f"signin:fails:{account}:{_digest(client_ip)}"
    locked = False
    if _increment(pair_key, window) >= settings.SIGN_IN_MAX_FAILURES:
        cache.set(pair_lock, now.timestamp() + window, timeout=window)
        cache.delete(pair_key)
        locked = True
    if (
        _increment(f"signin:fails:{account}", window)
        >= settings.SIGN_IN_ACCOUNT_MAX_FAILURES
    ):
        cache.set(account_lock, now.timestamp() + window, timeout=window)
        cache.delete(f"signin:fails:{account}")
        locked = True
    if locked:
        check_sign_in_lock(email, client_ip, now)


@fail_closed
def clear_sign_in_failures(email: str, client_ip: str) -> None:
    """A success clears the failures of this client only; the account-wide count runs out by itself."""
    cache.delete(f"signin:fails:{_digest(email)}:{_digest(client_ip)}")


# --- the email-code sends ---------------------------------------------------------------------


def _send_keys(email: str) -> tuple[str, str]:
    digest = _digest(email)
    return f"code:sends:{digest}", f"code:cooldown:{digest}"


def _note_send(email: str, now: datetime) -> None:
    """Remember that a code was sent to this address and start its wrong-try count afresh.

    A real code starts over with five tries when a new one is sent; an address with nothing to verify
    must do the same, or the two could be told apart (:func:`phantom_code_attempt`).
    """
    digest = _digest(email)
    lifetime = settings.EMAIL_CODE_LIFETIME_SECONDS
    cache.set(f"code:last_sent:{digest}", now.timestamp(), timeout=lifetime)
    cache.delete(f"code:phantom:{digest}")


@fail_closed
def reserve_code_send(email: str, now: datetime | None = None) -> None:
    """Claim one email-code send for this address, or raise ``429``.

    ``resend_limit_reached`` when the hourly limit is used up, ``resend_too_soon`` inside the cooldown.
    A registration, a resend and every later code flow draw on the same budget. A refused attempt is
    not counted as a send.
    """
    now = now or timezone.now()
    sends_key, cooldown_key = _send_keys(email)
    count, wait = hit(sends_key, HOUR, now)
    if count > settings.EMAIL_CODE_SENDS_PER_HOUR:
        raise throttled(
            ErrorCode.RESEND_LIMIT_REACHED,
            wait,
            "Too many codes were requested. Try again later.",
        )
    cooldown = settings.EMAIL_CODE_RESEND_COOLDOWN_SECONDS
    unlock = now.timestamp() + cooldown
    if cache.add(cooldown_key, unlock, timeout=cooldown):
        _note_send(email, now)
        return
    current = cache.get(cooldown_key)
    if current is None or current <= now.timestamp():
        cache.set(cooldown_key, unlock, timeout=cooldown)  # the clock says it is over
        _note_send(email, now)
        return
    with contextlib.suppress(ValueError):  # the window may have ended meanwhile
        cache.decr(sends_key)
    raise throttled(
        ErrorCode.RESEND_TOO_SOON,
        _seconds_until(current, now),
        "A code was sent a moment ago.",
    )


@fail_closed
def resend_wait(email: str, now: datetime | None = None) -> int:
    """Seconds until another code may be sent to this address (at least 1)."""
    now = now or timezone.now()
    sends_key, cooldown_key = _send_keys(email)
    started = cache.get(f"{sends_key}:start")
    if (
        started is not None
        and started + HOUR > now.timestamp()
        and cache.get(sends_key, 0) >= settings.EMAIL_CODE_SENDS_PER_HOUR
    ):
        return _seconds_until(started + HOUR, now)
    unlock = cache.get(cooldown_key)
    if unlock is not None and unlock > now.timestamp():
        return _seconds_until(unlock, now)
    return 1


@fail_closed
def phantom_code_attempt(email: str, now: datetime | None = None) -> int | None:
    """Verify a code for an address that has nothing to verify, exactly as for a real pending code.

    A real code is live for ten minutes after it was sent, counts its wrong tries, and starts over
    when a new one is sent. This keeps the same state for every address, from the sends recorded in
    :func:`reserve_code_send`, so a pending account cannot be told from an unknown or an already
    registered address by what verifying says. Returns the number of tries used including this one,
    or ``None`` when no code is live (nothing was sent in the last ten minutes), which a real
    pending account answers as an expired code.
    """
    now = now or timezone.now()
    digest = _digest(email)
    lifetime = settings.EMAIL_CODE_LIFETIME_SECONDS
    sent = cache.get(f"code:last_sent:{digest}")
    if sent is None or sent + lifetime <= now.timestamp():
        return None
    return _increment(f"code:phantom:{digest}", lifetime)
