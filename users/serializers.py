from typing import Any
from zoneinfo import available_timezones

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from .models import Language, Platform, User
from .validators import normalize_password

# Computed once: the list of IANA names does not change while the process runs.
_TIME_ZONES = frozenset(available_timezones())


class DeviceSerializer(serializers.Serializer):  # type: ignore[misc]
    """The label of the device, for the user's own "active sessions" screen (ADR 0009)."""

    device_id = serializers.UUIDField(
        help_text="A random UUID the app creates once at install and keeps in secure storage."
    )
    device_name = serializers.CharField(
        max_length=100, help_text="A readable model name such as `Pixel 8`."
    )
    platform = serializers.ChoiceField(choices=Platform.choices)
    os_version = serializers.CharField(
        max_length=50, required=False, allow_blank=True, default=""
    )
    app_version = serializers.CharField(
        max_length=50, help_text="For example `1.0.0+1`."
    )


class ClientSettingsMixin(serializers.Serializer):  # type: ignore[misc]
    """``language`` and ``time_zone``: the device the user signs in on decides them."""

    language = serializers.ChoiceField(
        choices=Language.choices,
        required=False,
        help_text="`en` or `ar`; the app sends `en` when the device language is neither.",
    )
    time_zone = serializers.CharField(
        max_length=64,
        required=False,
        help_text="An IANA name such as `Europe/Moscow`. Leave it out when the device cannot read it.",
    )

    def validate_time_zone(self, value: str) -> str:
        if value not in _TIME_ZONES:
            raise serializers.ValidationError("Not a known time zone.", code="invalid")
        return value


class PasswordField(serializers.CharField):  # type: ignore[misc]
    """A password: never trimmed, always NFKC-normalized, so it is one value in every flow."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("trim_whitespace", False)
        kwargs.setdefault("write_only", True)
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> str:
        return normalize_password(super().to_internal_value(data))


def password_errors(password: str, user: User) -> list[ErrorDetail]:
    """Run every configured password rule; each failed rule is its own error with its own code."""
    try:
        validate_password(password, user)
    except DjangoValidationError as error:
        return [
            ErrorDetail(message, code=item.code or "invalid")
            for item in error.error_list
            for message in item.messages
        ]
    return []


class RegisterSerializer(ClientSettingsMixin):
    name = serializers.CharField(max_length=100)
    email = serializers.EmailField(max_length=254)
    password = PasswordField(
        help_text="At least 15 characters; no other composition rule. It must not resemble the name or "
        "email, be a very common password or appear in a known data breach."
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        candidate = User(
            email=User.objects.normalize_email(attrs["email"]), name=attrs["name"]
        )
        errors = password_errors(attrs["password"], candidate)
        if errors:
            raise serializers.ValidationError({"password": errors})
        return attrs


class VerifyEmailSerializer(ClientSettingsMixin):
    email = serializers.EmailField(max_length=254)
    code = serializers.RegexField(
        rf"^[0-9]{{{settings.EMAIL_CODE_LENGTH}}}$",
        help_text=f"The {settings.EMAIL_CODE_LENGTH}-digit code from the email.",
    )
    device = DeviceSerializer()


class ResendCodeSerializer(serializers.Serializer):  # type: ignore[misc]
    email = serializers.EmailField(max_length=254)


class SignInSerializer(ClientSettingsMixin):
    email = serializers.EmailField(max_length=254)
    # No password policy on sign-in, and a blank one is let through so the email is judged first.
    password = PasswordField(allow_blank=True)
    captcha_token = serializers.CharField(
        max_length=2048,
        required=False,
        allow_blank=True,
        write_only=True,
        help_text=(
            "The token of a solved Cloudflare Turnstile widget created with the action `sign_in`. "
            "Required only after `captcha_required`; it is single use and lives five minutes."
        ),
    )
    device = DeviceSerializer()


class RefreshSerializer(serializers.Serializer):  # type: ignore[misc]
    refresh_token = serializers.CharField(max_length=2048)
    device = DeviceSerializer()


class SignOutSerializer(serializers.Serializer):  # type: ignore[misc]
    refresh_token = serializers.CharField(
        max_length=2048,
        required=False,
        help_text="Lets a device whose access token has expired end its own session without refreshing first.",
    )


# --- answers ------------------------------------------------------------------------------------


class CodeSentSerializer(serializers.Serializer):  # type: ignore[misc]
    code_expires_in = serializers.IntegerField(
        help_text="Seconds until the code expires."
    )
    resend_available_in = serializers.IntegerField(
        help_text="Seconds until a new code may be requested."
    )


class RegisterResponseSerializer(CodeSentSerializer):
    email = serializers.EmailField()


class UserSummarySerializer(serializers.ModelSerializer):  # type: ignore[misc]
    class Meta:
        model = User
        fields = ("id", "name", "email")
        read_only_fields = fields


class RefreshResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    access_token = serializers.CharField(
        help_text="Send it as `Authorization: Bearer`."
    )
    refresh_token = serializers.CharField(
        required=False,
        help_text="Single-use. Absent when the web form of the tokens carries it in a cookie.",
    )
    token_type = serializers.CharField(help_text="Always `Bearer`.")
    expires_in = serializers.IntegerField(
        help_text="Seconds the access token lives (600)."
    )


class SignedInResponseSerializer(RefreshResponseSerializer):
    user = UserSummarySerializer()


class ProfileSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    class Meta:
        model = User
        fields = ("id", "name", "email", "language", "time_zone")
        read_only_fields = fields
