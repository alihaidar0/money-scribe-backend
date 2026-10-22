import contextlib
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.exceptions import APIException, Throttled

from conftest import Clock
from users import limits

EMAIL = "person@example.com"
HOME = "203.0.113.1"
ELSEWHERE = "198.51.100.9"


def wait_of(error: pytest.ExceptionInfo[Throttled]) -> int:
    return int(error.value.wait or 0)


def fail(email: str = EMAIL, client_ip: str = HOME, times: int = 1) -> None:
    for _ in range(times):
        with contextlib.suppress(Throttled):
            limits.record_sign_in_failure(email, client_ip)


# --- the sign-in lock -------------------------------------------------------------------------


def test_failures_below_the_limit_do_not_lock(clock: Clock) -> None:
    fail(times=4)

    limits.check_sign_in_lock(EMAIL, HOME)


def test_the_failure_that_reaches_the_limit_locks_and_answers_429(clock: Clock) -> None:
    fail(times=4)

    with pytest.raises(Throttled) as error:
        limits.record_sign_in_failure(EMAIL, HOME)

    assert error.value.detail.code == "sign_in_locked"
    assert wait_of(error) == 900
    with pytest.raises(Throttled):
        limits.check_sign_in_lock(EMAIL, HOME)


def test_the_lock_counts_down(clock: Clock) -> None:
    fail(times=5)

    clock.advance(seconds=100)
    with pytest.raises(Throttled) as error:
        limits.check_sign_in_lock(EMAIL, HOME)

    assert wait_of(error) == 800


def test_the_lock_is_over_when_the_clock_says_so(clock: Clock) -> None:
    fail(times=5)

    clock.advance(seconds=900)

    limits.check_sign_in_lock(EMAIL, HOME)


def test_the_lock_holds_only_the_client_that_failed(clock: Clock) -> None:
    """Someone who knows an address cannot lock its owner out from another network."""
    fail(times=5, client_ip=ELSEWHERE)

    with pytest.raises(Throttled):
        limits.check_sign_in_lock(EMAIL, ELSEWHERE)
    limits.check_sign_in_lock(EMAIL, HOME)


def test_failures_from_every_address_together_lock_the_account_for_everyone(
    clock: Clock,
) -> None:
    for number in range(49):
        limits.record_sign_in_failure(EMAIL, f"198.51.100.{number}")
    limits.check_sign_in_lock(EMAIL, HOME)

    with pytest.raises(Throttled) as error:
        limits.record_sign_in_failure(EMAIL, "198.51.100.200")

    assert error.value.detail.code == "sign_in_locked"
    with pytest.raises(Throttled):
        limits.check_sign_in_lock(
            EMAIL, HOME
        )  # an address that never failed is held too


def test_a_success_clears_the_failures_of_that_client(clock: Clock) -> None:
    fail(times=4)
    limits.clear_sign_in_failures(EMAIL, HOME)

    fail(times=4)

    limits.check_sign_in_lock(EMAIL, HOME)


def test_accounts_are_counted_separately(clock: Clock) -> None:
    fail(times=5)

    limits.check_sign_in_lock("someone-else@example.com", HOME)


# --- the fixed window -------------------------------------------------------------------------


def test_hit_counts_in_a_fixed_window_and_reports_the_wait(clock: Clock) -> None:
    assert limits.hit("probe", 60) == (1, 60)
    clock.advance(seconds=20)

    assert limits.hit("probe", 60) == (2, 40)
    assert limits.hit("probe", 60) == (3, 40)


def test_hit_starts_again_when_the_window_is_over(clock: Clock) -> None:
    limits.hit("probe", 60)
    limits.hit("probe", 60)
    clock.advance(seconds=60)

    assert limits.hit("probe", 60) == (1, 60)


def test_hit_recovers_when_the_counter_expires_between_the_two_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def vanished(key: str, delta: int = 1) -> int:
        raise ValueError(key)

    monkeypatch.setattr(cache, "incr", vanished)

    assert limits.hit("probe", 60)[0] == 1


# --- the email-code sends ---------------------------------------------------------------------


def test_the_second_send_inside_the_cooldown_is_too_soon(clock: Clock) -> None:
    limits.reserve_code_send(EMAIL)
    clock.advance(seconds=20)

    with pytest.raises(Throttled) as error:
        limits.reserve_code_send(EMAIL)

    assert error.value.detail.code == "resend_too_soon"
    assert wait_of(error) == 40


def test_a_send_after_the_cooldown_is_allowed(clock: Clock) -> None:
    limits.reserve_code_send(EMAIL)
    clock.advance(seconds=60)

    limits.reserve_code_send(EMAIL)


def test_refused_attempts_are_not_counted_as_sends(clock: Clock) -> None:
    limits.reserve_code_send(EMAIL)
    for _ in range(3):
        with pytest.raises(Throttled):
            limits.reserve_code_send(EMAIL)
    for _ in range(4):
        clock.advance(seconds=61)
        limits.reserve_code_send(
            EMAIL
        )  # five real sends in all: still inside the limit

    clock.advance(seconds=61)
    with pytest.raises(Throttled) as error:
        limits.reserve_code_send(EMAIL)
    assert error.value.detail.code == "resend_limit_reached"


def test_the_hourly_limit_names_its_own_code_and_wait(clock: Clock) -> None:
    for _ in range(5):
        limits.reserve_code_send(EMAIL)
        clock.advance(seconds=61)

    with pytest.raises(Throttled) as error:
        limits.reserve_code_send(EMAIL)

    assert error.value.detail.code == "resend_limit_reached"
    # The window opened with the first send; 305 s have passed.
    assert wait_of(error) == 3600 - 5 * 61


def test_the_hourly_window_ends(clock: Clock) -> None:
    for _ in range(5):
        limits.reserve_code_send(EMAIL)
        clock.advance(seconds=61)
    clock.current += timedelta(hours=1)

    limits.reserve_code_send(EMAIL)


@pytest.mark.parametrize(
    ("elapsed", "expected"), [(0, 60), (45, 15), (60, 1), (500, 1)]
)
def test_resend_wait_is_at_least_one_second(
    clock: Clock, elapsed: int, expected: int
) -> None:
    limits.reserve_code_send(EMAIL)
    clock.advance(seconds=elapsed)

    assert limits.resend_wait(EMAIL) == expected


def test_resend_wait_without_a_send_is_one_second(clock: Clock) -> None:
    assert limits.resend_wait(EMAIL) == 1


def test_resend_wait_after_the_hourly_limit_is_the_long_wait(clock: Clock) -> None:
    for _ in range(5):
        limits.reserve_code_send(EMAIL)
        clock.advance(seconds=61)

    assert limits.resend_wait(EMAIL) == 3600 - 5 * 61


def test_an_address_with_nothing_sent_has_no_live_code(clock: Clock) -> None:
    assert limits.phantom_code_attempt(EMAIL) is None


def test_phantom_attempts_count_per_address_once_a_code_was_sent(clock: Clock) -> None:
    limits.reserve_code_send(EMAIL)

    assert [limits.phantom_code_attempt(EMAIL) for _ in range(3)] == [1, 2, 3]
    assert limits.phantom_code_attempt("other@example.com") is None


def test_the_phantom_code_expires_with_the_code_lifetime(clock: Clock) -> None:
    limits.reserve_code_send(EMAIL)
    clock.advance(seconds=599)
    assert limits.phantom_code_attempt(EMAIL) == 1

    clock.advance(seconds=1)

    assert limits.phantom_code_attempt(EMAIL) is None


def test_a_new_send_starts_the_phantom_count_afresh(clock: Clock) -> None:
    limits.reserve_code_send(EMAIL)
    for _ in range(3):
        limits.phantom_code_attempt(EMAIL)
    clock.advance(seconds=61)
    limits.reserve_code_send(EMAIL)

    assert limits.phantom_code_attempt(EMAIL) == 1


# --- failing closed ---------------------------------------------------------------------------


class BrokenCache:
    """A cache whose server is gone: every operation raises the way redis-py does."""

    def __getattr__(self, name: str) -> Any:
        def broken(*args: Any, **kwargs: Any) -> None:
            raise RedisConnectionError("connection refused")

        return broken


@pytest.fixture
def broken_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "cache", BrokenCache())


@pytest.mark.parametrize(
    "call",
    [
        lambda: limits.check_sign_in_lock(EMAIL, HOME),
        lambda: limits.record_sign_in_failure(EMAIL, HOME),
        lambda: limits.record_address_failure(HOME),
        lambda: limits.challenge_required(HOME),
        lambda: limits.clear_sign_in_failures(EMAIL, HOME),
        lambda: limits.reserve_code_send(EMAIL),
        lambda: limits.resend_wait(EMAIL),
        lambda: limits.phantom_code_attempt(EMAIL),
        lambda: limits.hit("probe", 60),
    ],
)
def test_every_limit_refuses_with_503_when_the_store_is_down(
    broken_cache: None, call: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(APIException) as error:
        call()

    assert error.value.status_code == 503
    assert error.value.detail.code == "service_unavailable"
    assert error.value.wait == 30
    assert "connection refused" not in caplog.text
    assert EMAIL not in caplog.text


def test_a_scope_without_a_rate_is_not_throttled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from users.throttles import SignInThrottle

    monkeypatch.setattr(SignInThrottle, "THROTTLE_RATES", {"sign_in": None})

    assert SignInThrottle().allow_request(None, None) is True
