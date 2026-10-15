import pytest
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from users.validators import DigitPasswordValidator


def test_password_with_a_digit_is_accepted() -> None:
    DigitPasswordValidator().validate("letters-and-1")


@pytest.mark.parametrize("password", ["no-digits-here", "", "ÄÖÜ-абв-ابج"])
def test_password_without_a_digit_is_rejected(password: str) -> None:
    with pytest.raises(ValidationError) as error:
        DigitPasswordValidator().validate(password)
    assert error.value.code == "password_no_digit"


def test_non_latin_digits_count_as_digits() -> None:
    DigitPasswordValidator().validate("password-٣")


def test_help_text_mentions_the_digit() -> None:
    assert "digit" in DigitPasswordValidator().get_help_text()


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("short1", "password_too_short"),
        ("longenoughbutnodigit", "password_no_digit"),
        ("password1", "password_too_common"),
    ],
)
def test_configured_validators_enforce_the_rules(password: str, code: str) -> None:
    with pytest.raises(ValidationError) as error:
        validate_password(password)
    assert code in {item.code for item in error.value.error_list}


def test_a_valid_password_passes_every_configured_validator() -> None:
    validate_password("a-fine-passphrase-7")
