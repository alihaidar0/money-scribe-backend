import logging
import urllib.error
from typing import Any

import pytest
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from users.models import User
from users.validators import (
    CompromisedPasswordValidator,
    MaximumLengthPasswordValidator,
    breach_count,
    normalize_password,
)

PASSWORD = "correct horse battery staple"


# Known answers: the SHA-1 of each text split after five characters, the way the range API wants it.
# Fixed values, not computed here, so the tests check the implementation instead of repeating it.
SHA1_PARTS = {
    "password": ("5BAA6", "1E4C9B93F3F0682250B6CF8331B7EE68FD8"),
    PASSWORD: ("ABF7A", "AD6438836DBE526AA231ABDE2D0EEF74D42"),
}


def sha1_parts(text: str) -> tuple[str, str]:
    return SHA1_PARTS[text]


class FakePwnedService:
    """Stands in for the Pwned Passwords range API: records what was asked, answers ``body``."""

    def __init__(self, timeout: float) -> None:
        self.asked: list[str] = []
        self.body = ""
        self.timeout = timeout

    def __call__(self, request: Any, timeout: float) -> FakePwnedService:
        self.asked.append(request.full_url)
        assert timeout == self.timeout
        assert request.get_header("Add-padding") == "true"
        return self

    def __enter__(self) -> FakePwnedService:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body.encode()


@pytest.fixture
def pwned(monkeypatch: pytest.MonkeyPatch, settings: Any) -> FakePwnedService:
    """Turn the breach check on and stand in for the service."""
    settings.PWNED_PASSWORD_CHECK = True
    service = FakePwnedService(settings.PWNED_PASSWORD_TIMEOUT_SECONDS)
    monkeypatch.setattr("urllib.request.urlopen", service)
    return service


# --- the length rules and the policy ----------------------------------------------------------


def test_a_password_of_128_characters_is_accepted_and_one_more_is_not() -> None:
    MaximumLengthPasswordValidator().validate("a" * 128)

    with pytest.raises(ValidationError) as error:
        MaximumLengthPasswordValidator().validate("a" * 129)
    assert error.value.code == "password_too_long"


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("a" * 14, "password_too_short"),
        ("1q2w3e4r5t6y7u8i9o0p", "password_too_common"),
        ("a" * 129, "password_too_long"),
    ],
)
def test_configured_validators_enforce_the_rules(password: str, code: str) -> None:
    with pytest.raises(ValidationError) as error:
        validate_password(password)
    assert code in {item.code for item in error.value.error_list}


def test_the_password_may_resemble_nothing_of_the_user() -> None:
    user = User(email="sara@example.com", name="Sara Example")

    with pytest.raises(ValidationError) as error:
        validate_password("sara@example.com!!!!", user)

    assert "password_too_similar" in {item.code for item in error.value.error_list}


@pytest.mark.parametrize(
    "password",
    [
        PASSWORD,  # no digit, no symbol, no capital: composition rules are gone
        "a" * 15,
        "x" * 128,
        "пароль для моей жизни и денег",
        "كلمة مرور طويلة جدا للتطبيق",
        "  leading and trailing spaces are kept  ",
    ],
)
def test_there_are_no_composition_rules(password: str) -> None:
    validate_password(password, User(email="sara@example.com", name="Sara Example"))


# --- normalization ----------------------------------------------------------------------------


def test_a_password_is_normalized_to_nfkc() -> None:
    fullwidth = "".join(
        chr(ord(c) + 0xFEE0) if c != " " else "　" for c in "good passphrase today"
    )

    assert normalize_password(fullwidth) == "good passphrase today"
    assert normalize_password("é") == "é"  # a letter and its combining accent
    assert normalize_password("plain") == "plain"


# --- the breach check -------------------------------------------------------------------------


def test_a_breached_password_is_refused_and_only_a_prefix_leaves(
    pwned: FakePwnedService,
) -> None:
    prefix, suffix = sha1_parts("password")
    pwned.body = f"0018A45C4D1DEF81644B54AB7F969B88D65:3\r\n{suffix}:9545824\r\n"

    with pytest.raises(ValidationError) as error:
        CompromisedPasswordValidator().validate("password")

    assert error.value.code == "password_compromised"
    assert pwned.asked == [f"https://api.pwnedpasswords.com/range/{prefix}"]
    assert len(prefix) == 5


def test_a_password_that_is_not_in_the_answer_passes(pwned: FakePwnedService) -> None:
    pwned.body = "0018A45C4D1DEF81644B54AB7F969B88D65:3\r\n"

    CompromisedPasswordValidator().validate(PASSWORD)


def test_a_padding_entry_with_a_zero_count_is_not_a_breach(
    pwned: FakePwnedService,
) -> None:
    _, suffix = sha1_parts(PASSWORD)
    pwned.body = f"{suffix}:0\r\n"

    assert breach_count(PASSWORD) == 0
    CompromisedPasswordValidator().validate(PASSWORD)


def test_an_unreachable_service_never_blocks_a_sign_up(
    monkeypatch: pytest.MonkeyPatch, settings: Any, caplog: pytest.LogCaptureFixture
) -> None:
    settings.PWNED_PASSWORD_CHECK = True

    def down(request: Any, timeout: float) -> None:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", down)

    with caplog.at_level(logging.WARNING, logger="users.validators"):
        CompromisedPasswordValidator().validate(PASSWORD)

    assert breach_count(PASSWORD) is None
    assert "could not be reached" in caplog.text
    assert PASSWORD not in caplog.text


def test_the_check_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.PWNED_PASSWORD_CHECK = False

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("no call may be made")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)

    CompromisedPasswordValidator().validate("password")


def test_the_breach_check_is_part_of_the_configured_rules(
    pwned: FakePwnedService,
) -> None:
    prefix, suffix = sha1_parts(PASSWORD)
    pwned.body = f"{suffix}:12\r\n"

    with pytest.raises(ValidationError) as error:
        validate_password(PASSWORD)

    assert {item.code for item in error.value.error_list} == {"password_compromised"}
    assert pwned.asked == [f"https://api.pwnedpasswords.com/range/{prefix}"]


def test_help_texts_are_present() -> None:
    assert "128" in MaximumLengthPasswordValidator().get_help_text()
    assert "breach" in CompromisedPasswordValidator().get_help_text()
