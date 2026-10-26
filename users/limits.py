"""Counters and cooldowns kept in the cache, which is Redis in production (ADR 0006).

Three properties matter for a brake against guessing:

* **Atomic.** A count is raised with one ``incr`` and a cooldown is claimed with one ``add`` (``SET NX``),
  never with a read followed by a write, so concurrent requests cannot slip past a limit together.
  The windows are fixed windows, which allow a short burst at a window boundary.
* **Fail closed.** If the store is down, the request is refused with ``503`` and ``Retry-After``
  (:func:`fail_closed`). Letting it through would switch the brake off exactly when it is least
  watched. Calls that do not use a limit (a refresh, a request with an access token) are unaffected.
* **No oracle, except at sign-in.** The email-code limits are keyed by the normalized email address
  whether or not an account exists, so an unknown address behaves exactly like a known one. Sign-in is
  the one deliberate exception (ADR 0012): it names the field at fault, and the client address limit
  stands in for the per-email count of an address that has no account.
"""

import contextlib
import functools
import hashlib
import logging
import math
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import NoReturn

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


def count_hit(key: str, window: int, now: datetime | None = None) -> tuple[int, int]:
    """Count one request in a fixed window; returns the count so far and the seconds until the window ends.

    The counter and the window's start are separate keys. If the store still holds a counter that the
    clock says is over (or lost its start), the window begins again at one. A failure of the store is a
    ``RedisError``: :func:`hit` turns it into ``503``, and the per-user limits that fail open (ADR 0021)
    call this one and let the request through.
    """
    now = now or timezone.now()
    count = _increment(key, window)
    started = cache.get(f"{key}:start")
    if started is None or started + window <= now.timestamp():
        cache.set(key, 1, timeout=window)
        cache.set(f"{key}:start", now.timestamp(), timeout=window)
        count, started = 1, now.timestamp()
    return count, _seconds_until(started + window, now)


hit = fail_closed(count_hit)


# --- the sign-in lock -------------------------------------------------------------------------


def _address_lock_key(client_ip: str) -> str:
    return f"signin:addrlock:{_digest(client_ip)}"


def _lock_keys(email: str, client_ip: str) -> tuple[str, str, str]:
    """The lock of this account for this client address, of the account for everyone, of the address."""
    account = _digest(email)
    return (
        f"signin:lock:{account}:{_digest(client_ip)}",
        f"signin:lock:{account}",
        _address_lock_key(client_ip),
    )


def _raise_if_locked(keys: tuple[str, ...], now: datetime) -> None:
    unlocks = [cache.get(key) for key in keys]
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
def check_sign_in_lock(email: str, client_ip: str, now: datetime | None = None) -> None:
    """Raise ``sign_in_locked`` (429) while this account or this client address is locked."""
    _raise_if_locked(_lock_keys(email, client_ip), now or timezone.now())


def _count_address_failure(client_ip: str, now: datetime) -> bool:
    """Count a failed sign-in against the client address; ``True`` when that locks the address.

    Sign-in says whether an email has an account (ADR 0012), so the address is the brake against
    trying a list of emails: it counts every failure over all accounts, and a success does not clear
    it, or one real account would reset the count of a list-tester.
    """
    window = settings.SIGN_IN_LOCK_SECONDS
    address = _digest(client_ip)
    if _increment(f"signin:addrfails:{address}", window) < (
        settings.SIGN_IN_ADDRESS_MAX_FAILURES
    ):
        return False
    cache.set(_address_lock_key(client_ip), now.timestamp() + window, timeout=window)
    cache.delete(f"signin:addrfails:{address}")
    logger.warning("A client address was locked after repeated failed sign-ins.")
    return True


@fail_closed
def record_address_failure(client_ip: str, now: datetime | None = None) -> None:
    """Count a failed sign-in against the client address only: an email that has no account, or a
    bot challenge that failed.

    Nothing is kept per email: an address could otherwise fill the cache with keys of its choosing.
    Raises 429 if this failure locks the address.
    """
    now = now or timezone.now()
    if _count_address_failure(client_ip, now):
        _raise_if_locked((_address_lock_key(client_ip),), now)


@fail_closed
def challenge_required(client_ip: str) -> bool:
    """Whether this client address has failed enough sign-ins to be asked for a bot challenge.

    Only reads the count, so asking never counts as a failure. The count is the one of the address
    lock, so the challenge comes before the lock: 5 failures, then 10 more with a challenge to get past.
    """
    failures = cache.get(f"signin:addrfails:{_digest(client_ip)}", 0)
    return bool(failures >= settings.SIGN_IN_CHALLENGE_AFTER_FAILURES)


@fail_closed
def record_sign_in_failure(
    email: str, client_ip: str, now: datetime | None = None
) -> None:
    """Count a failed sign-in for an account, and raise 429 if it locks anything.

    Five failures from one address lock the pair of account and address. Fifty from all addresses
    together lock the account for everyone, which only a distributed guess can reach. Fifteen from
    one address over all accounts lock the address.
    """
    now = now or timezone.now()
    window = settings.SIGN_IN_LOCK_SECONDS
    account = _digest(email)
    pair_lock, account_lock, _ = _lock_keys(email, client_ip)
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
    if _count_address_failure(client_ip, now):
        locked = True
    if locked:
        check_sign_in_lock(email, client_ip, now)


@fail_closed
def clear_sign_in_failures(email: str, client_ip: str) -> None:
    """A success clears the failures of this client only; the account-wide count runs out by itself."""
    cache.delete(f"signin:fails:{_digest(email)}:{_digest(client_ip)}")


def clear_account_sign_in_lock(email: str) -> None:
    """A completed password reset lifts the account-wide lock and its count (ADR 0014).

    Anyone who knows an address can reach the 50 failures that lock the account for everyone; the reset proves the
    mailbox, so its owner gets back in at once. The locks of one client address stay. Best effort: the password has
    already changed, so a store outage is only logged.
    """
    account = _digest(email)
    try:
        cache.delete_many([f"signin:fails:{account}", f"signin:lock:{account}"])
    except RedisError:
        logger.warning(
            "The limit store is unavailable; the account lock was not lifted."
        )


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


@fail_closed
def reserve_email_change(user_id: uuid.UUID, now: datetime | None = None) -> None:
    """Count one email change started by this account, or raise ``429 resend_limit_reached``.

    The code of a change is mailed to an address the user chooses, so without a cap per account a
    signed-in user could use the call to send mail to any number of strangers. The cap counts every
    attempt, including the ones refused afterwards, so it also bounds how many addresses can be probed.
    """
    count, wait = hit(f"emailchange:{_digest(str(user_id))}", HOUR, now)
    if count > settings.EMAIL_CHANGE_REQUESTS_PER_HOUR:
        raise throttled(
            ErrorCode.RESEND_LIMIT_REACHED,
            wait,
            "Too many email changes were started. Try again later.",
        )


# --- the password confirmation lock -----------------------------------------------------------


def _confirm_keys(user_id: uuid.UUID) -> tuple[str, str]:
    digest = _digest(str(user_id))
    return f"confirm:fails:{digest}", f"confirm:lock:{digest}"


def _raise_confirm_locked(unlock: float, now: datetime) -> NoReturn:
    raise throttled(
        ErrorCode.PASSWORD_ATTEMPTS_EXCEEDED,
        _seconds_until(unlock, now),
        "Too many wrong passwords. Try again later.",
    )


@fail_closed
def check_password_lock(user_id: uuid.UUID, now: datetime | None = None) -> None:
    """Raise ``password_attempts_exceeded`` (429) while this account is locked out of confirming.

    Changing the password and deleting the account both ask for the current password. Someone holding
    only a stolen access token must not be able to guess it there, so wrong answers are counted per
    account, whatever the client address. The lock does not touch sign-in.
    """
    now = now or timezone.now()
    unlock = cache.get(_confirm_keys(user_id)[1])
    if unlock is not None and unlock > now.timestamp():
        _raise_confirm_locked(unlock, now)


@fail_closed
def record_password_failure(user_id: uuid.UUID, now: datetime | None = None) -> None:
    """Count a wrong current password; the failure that reaches the limit locks the account and raises 429."""
    now = now or timezone.now()
    window = settings.PASSWORD_CONFIRM_LOCK_SECONDS
    fails_key, lock_key = _confirm_keys(user_id)
    if _increment(fails_key, window) < settings.PASSWORD_CONFIRM_MAX_FAILURES:
        return
    unlock = now.timestamp() + window
    cache.set(lock_key, unlock, timeout=window)
    cache.delete(fails_key)
    logger.warning("An account was locked out of password confirmation.")
    _raise_confirm_locked(unlock, now)


@fail_closed
def clear_password_failures(user_id: uuid.UUID) -> None:
    """A right password starts the count again."""
    cache.delete(_confirm_keys(user_id)[0])


# --- the device key sign-in lock (ADR 0018) ---------------------------------------------------


def _device_keys(device_id: uuid.UUID, client_ip: str) -> tuple[str, str, str, str]:
    """The failure counters and the locks of one device and of one client address."""
    device, address = _digest(str(device_id)), _digest(client_ip)
    return (
        f"devicekey:fails:{device}",
        f"devicekey:lock:{device}",
        f"devicekey:addrfails:{address}",
        f"devicekey:addrlock:{address}",
    )


@fail_closed
def check_device_sign_in_lock(
    device_id: uuid.UUID, client_ip: str, now: datetime | None = None
) -> None:
    """Raise ``sign_in_locked`` (429) while this device or this client address is locked out of key sign-in.

    These counters are their own: failing here never locks the password sign-in, and a locked password
    sign-in does not stop a phone that holds a good key.
    """
    now = now or timezone.now()
    _, device_lock, _, address_lock = _device_keys(device_id, client_ip)
    _raise_if_locked((device_lock, address_lock), now)


@fail_closed
def record_device_sign_in_failure(
    device_id: uuid.UUID, client_ip: str, now: datetime | None = None
) -> None:
    """Count a failed key sign-in, and raise 429 if that failure locks the device or the address.

    The device is the brake against guessing one phone's key (which cannot be guessed: it is a signature),
    and the address is the brake against a client that sends many devices to find one that exists.
    """
    now = now or timezone.now()
    window = settings.SIGN_IN_LOCK_SECONDS
    device_fails, device_lock, address_fails, address_lock = _device_keys(
        device_id, client_ip
    )
    locked = False
    if _increment(device_fails, window) >= settings.DEVICE_KEY_SIGN_IN_MAX_FAILURES:
        cache.set(device_lock, now.timestamp() + window, timeout=window)
        cache.delete(device_fails)
        locked = True
    if _increment(address_fails, window) >= settings.DEVICE_KEY_ADDRESS_MAX_FAILURES:
        cache.set(address_lock, now.timestamp() + window, timeout=window)
        cache.delete(address_fails)
        locked = True
    if locked:
        logger.warning("A device key sign-in was locked after repeated failures.")
        _raise_if_locked((device_lock, address_lock), now)


@fail_closed
def clear_device_sign_in_failures(device_id: uuid.UUID) -> None:
    """A success starts the count of this device afresh; the address count runs out by itself."""
    cache.delete(f"devicekey:fails:{_digest(str(device_id))}")


def allow_new_device_email(user_id: uuid.UUID, now: datetime | None = None) -> bool:
    """Whether one more new-device email may go to this account in this hour (ADR 0009).

    It never fails a sign-in: if the limit store is down the email is simply not sent, which is the safe side of a
    cap whose job is to stop a flood.
    """
    try:
        count, _ = count_hit(f"newdevice:{_digest(str(user_id))}", HOUR, now)
    except RedisError:
        logger.warning("The limit store is unavailable; no new-device email is sent.")
        return False
    return count <= settings.NEW_DEVICE_EMAILS_PER_HOUR
