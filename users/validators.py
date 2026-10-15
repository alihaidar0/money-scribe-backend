from django.contrib.auth.base_user import AbstractBaseUser
from django.core.exceptions import ValidationError


class DigitPasswordValidator:
    """Require at least one digit in the password."""

    def validate(self, password: str, user: AbstractBaseUser | None = None) -> None:
        if not any(character.isdecimal() for character in password):
            raise ValidationError(
                "The password must contain at least one digit.",
                code="password_no_digit",
            )

    def get_help_text(self) -> str:
        return "Your password must contain at least one digit."
