import base64
import binascii
from typing import Any
from zoneinfo import available_timezones

from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from common.serializers import TextField

from .models import DeviceKey, Language, Platform, User
from .validators import normalize_password, password_errors

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


class RegisterSerializer(ClientSettingsMixin):
    name = TextField(max_length=100)
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


class PasswordResetRequestSerializer(serializers.Serializer):  # type: ignore[misc]
    email = serializers.EmailField(max_length=254)


class PasswordResetVerifySerializer(serializers.Serializer):  # type: ignore[misc]
    email = serializers.EmailField(max_length=254)
    code = serializers.RegexField(
        rf"^[0-9]{{{settings.EMAIL_CODE_LENGTH}}}$",
        help_text=f"The {settings.EMAIL_CODE_LENGTH}-digit code from the email.",
    )


class PasswordResetConfirmSerializer(serializers.Serializer):  # type: ignore[misc]
    reset_token = serializers.CharField(
        max_length=128,
        write_only=True,
        help_text="The `reset_token` that `auth_password_reset_verify` returned. It works once.",
    )
    password = PasswordField(
        help_text="The new password, under the same rules as at registration."
    )


class ChangePasswordSerializer(serializers.Serializer):  # type: ignore[misc]
    current_password = PasswordField(
        max_length=1024, help_text="The password in use now."
    )
    password = PasswordField(
        help_text="The new password, under the same rules as at registration. It must differ from "
        "the current one."
    )


class ProfileUpdateSerializer(serializers.Serializer):  # type: ignore[misc]
    """Only the name can be edited here; any other field is refused instead of silently ignored."""

    name = TextField(
        max_length=100,
        help_text="The display name, 1 to 100 characters, trimmed.",
    )

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            refused = {
                field: [
                    ErrorDetail("This field cannot be changed here.", code="read_only")
                ]
                for field in data
                if field not in self.fields
            }
            if refused:
                raise serializers.ValidationError(refused)
        value: dict[str, Any] = super().to_internal_value(data)
        return value


class EmailChangeRequestSerializer(serializers.Serializer):  # type: ignore[misc]
    email = serializers.EmailField(max_length=254, help_text="The new address.")
    current_password = PasswordField(
        max_length=1024, help_text="The password in use now."
    )


class EmailChangeConfirmSerializer(serializers.Serializer):  # type: ignore[misc]
    code = serializers.RegexField(
        rf"^[0-9]{{{settings.EMAIL_CODE_LENGTH}}}$",
        help_text=f"The {settings.EMAIL_CODE_LENGTH}-digit code mailed to the new address.",
    )


class DeleteAccountSerializer(serializers.Serializer):  # type: ignore[misc]
    password = PasswordField(
        max_length=1024, help_text="The current password, to confirm the deletion."
    )


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


class ResetTokenResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    reset_token = serializers.CharField(
        help_text="Single use. Send it, with the new password, to `auth_password_reset_confirm`."
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds until the reset token expires (600)."
    )


class RegisterResponseSerializer(CodeSentSerializer):
    email = serializers.EmailField()


class EmailChangeResponseSerializer(CodeSentSerializer):
    email = serializers.EmailField(help_text="The new address the code was mailed to.")


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


# --- sign-in with a device key (ADR 0018) -------------------------------------------------------


class Base64Field(serializers.CharField):  # type: ignore[misc]
    """Standard base64 with padding, decoded strictly to bytes (no whitespace, no URL alphabet)."""

    def to_internal_value(self, data: Any) -> bytes:
        text = super().to_internal_value(data)
        try:
            raw = base64.b64decode(text, validate=True)
        except binascii.Error, ValueError:
            raise serializers.ValidationError(
                "Not valid base64.", code="invalid"
            ) from None
        if not raw:
            raise serializers.ValidationError("Empty.", code="blank")
        return raw


class DeviceKeyChallengeSerializer(serializers.Serializer):  # type: ignore[misc]
    device_id = serializers.UUIDField(
        help_text="The `device_id` of the app install (the same random id as in the `device` object)."
    )


class DeviceKeyRegisterSerializer(serializers.Serializer):  # type: ignore[misc]
    current_password = PasswordField(
        max_length=1024, help_text="The password in use now."
    )
    public_key = Base64Field(
        max_length=256,
        help_text="The public key of the device: P-256, SPKI DER in standard base64 with padding.",
    )
    algorithm = serializers.ChoiceField(
        choices=["ES256"], help_text="Only `ES256` (ECDSA on P-256 with SHA-256)."
    )
    challenge_id = serializers.UUIDField(
        help_text="The `challenge_id` of `auth_device_key_challenge`."
    )
    signature = Base64Field(
        max_length=256,
        help_text="The DER signature, in standard base64 with padding, of the registration text.",
    )
    attestation_chain = serializers.ListField(
        child=serializers.CharField(max_length=8192),
        required=False,
        allow_null=True,
        max_length=10,
        help_text=(
            "Android only: the certificate chain of the key attestation, leaf first, each certificate "
            "DER in standard base64. Required for an Android session; `null` or left out on iOS."
        ),
    )


class DeviceKeySignInSerializer(ClientSettingsMixin):
    challenge_id = serializers.UUIDField(
        help_text="The `challenge_id` of `auth_device_key_challenge`."
    )
    signature = Base64Field(
        max_length=256,
        help_text="The DER signature, in standard base64 with padding, of the sign-in text.",
    )
    device = DeviceSerializer()


class DeviceKeyChallengeResponseSerializer(serializers.Serializer):  # type: ignore[misc]
    challenge_id = serializers.UUIDField()
    challenge = serializers.CharField(
        help_text="32 random bytes in base64url without padding. Sign it exactly as sent."
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds the challenge lives (120). It works once."
    )


class DeviceKeySerializer(serializers.ModelSerializer):  # type: ignore[misc]
    is_current = serializers.SerializerMethodField(
        help_text="Whether this is the key of the device that made the request."
    )

    class Meta:
        model = DeviceKey
        fields = (
            "id",
            "device_id",
            "device_name",
            "platform",
            "attested",
            "is_current",
            "created_at",
            "last_used_at",
            "expires_at",
        )
        read_only_fields = fields

    def get_is_current(self, obj: DeviceKey) -> bool:
        return str(obj.device_id) == str(self.context.get("device_id", ""))


class DeviceKeyListSerializer(serializers.Serializer):  # type: ignore[misc]
    """The envelope of every list (ADR 0003). This list holds at most five keys, so it is one page."""

    next = serializers.CharField(
        allow_null=True, help_text="Always `null`: the list fits one page."
    )
    previous = serializers.CharField(
        allow_null=True, help_text="Always `null`: the list fits one page."
    )
    results = DeviceKeySerializer(many=True)
