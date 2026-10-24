import uuid
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .managers import UserManager


class Language(models.TextChoices):
    ENGLISH = "en", "English"
    ARABIC = "ar", "Arabic"


class User(AbstractBaseUser, PermissionsMixin):
    """The project's user: signs in with an email address, owns all financial data.

    Every model that holds user data points here with ``on_delete=CASCADE``, so
    deleting the account deletes everything the user owns.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # unique=True satisfies Django's USERNAME_FIELD check; the Lower() constraint below also
    # rejects addresses that differ only in case when they are written without save().
    email = models.EmailField("email address", unique=True)
    name = models.CharField(max_length=100)
    # Empty until the 6-digit email code is accepted; an unverified account cannot sign in.
    email_verified_at = models.DateTimeField(null=True, blank=True)
    # Chosen by the device the user signs in on (docs/api-fields.md): the language of the emails and
    # pushes, and the zone that decides the user's "today".
    language = models.CharField(
        max_length=2, choices=Language, default=Language.ENGLISH
    )
    time_zone = models.CharField(max_length=64, default="UTC")
    date_joined = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["name"]

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                Lower("email"), name="users_user_email_lower_unique"
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""), name="users_user_name_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        super().clean()
        self._normalize()

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._normalize()
        super().save(*args, **kwargs)

    def _normalize(self) -> None:
        self.email = UserManager.normalize_email(self.email)
        self.name = self.name.strip()


class EmailCodePurpose(models.TextChoices):
    VERIFY_EMAIL = "verify_email", "Verify email"
    PASSWORD_RESET = "password_reset", "Password reset"
    EMAIL_CHANGE = "email_change", "Email change"


class EmailCode(models.Model):
    """A 6-digit code sent by email (ADR 0006). Only a keyed hash of it is stored.

    A new code for the same purpose replaces the earlier ones: the sender deletes them, so at most
    one is live. ``attempts`` counts the wrong tries against this code.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_codes"
    )
    purpose = models.CharField(max_length=20, choices=EmailCodePurpose)
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    # What the code is for, when it is for something: the new address of an email change.
    payload = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "purpose", "-created_at"], name="users_emailcode_lookup"
            )
        ]

    def __str__(self) -> str:
        return f"{self.purpose} code of {self.user_id}"


class PasswordResetToken(models.Model):
    """The proof that a password reset code was accepted (ADR 0014). Only a hash of it is stored.

    One per user at most: a new one replaces the old. It works once and lives a few minutes, and it is
    deleted when the password changes, whichever way.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_token",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)

    def __str__(self) -> str:
        return f"password reset token of {self.user_id}"


class Platform(models.TextChoices):
    ANDROID = "android", "Android"
    IOS = "ios", "iOS"
    WEB = "web", "Web"


class EndReason(models.TextChoices):
    SIGNED_OUT = "signed_out", "Signed out"
    REVOKED_BY_USER = "revoked_by_user", "Ended by the user"
    PASSWORD_CHANGED = "password_changed", "Password changed"
    EMAIL_CHANGED = "email_changed", "Email changed"
    TOKEN_REUSE = "token_reuse", "Refresh token reused"
    EXPIRED = "expired", "Expired"
    ACCOUNT_DELETED = "account_deleted", "Account deleted"


class Session(models.Model):
    """One sign-in of one device (ADR 0006 and 0009).

    The access token carries the session id (``sid``), and every request loads the session, so ending
    it revokes access at once. The device fields are a label for the user's own screen, never a security
    check. ``ip`` is the client address truncated to its network (IPv4 /24, IPv6 /48).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessions"
    )
    device_id = models.UUIDField()
    device_name = models.CharField(max_length=100)
    platform = models.CharField(max_length=7, choices=Platform)
    os_version = models.CharField(max_length=50, blank=True)
    app_version = models.CharField(max_length=50)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    last_active_at = models.DateTimeField(default=timezone.now)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    # The refresh state of ADR 0006: the hash of the current refresh token, how many times it has
    # rotated, and when the last rotation happened (the replay window counts from it).
    refresh_hash = models.CharField(max_length=64)
    generation = models.PositiveIntegerField(default=0)
    rotated_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=20, choices=EndReason, blank=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "-last_active_at", "-id"], name="users_session_by_user"
            ),
            models.Index(fields=["ended_at"], name="users_session_ended"),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=(
                    models.Q(ended_at__isnull=True, end_reason="")
                    | models.Q(ended_at__isnull=False) & ~models.Q(end_reason="")
                ),
                name="users_session_end_reason_with_end",
            )
        ]

    def __str__(self) -> str:
        return f"{self.platform} session of {self.user_id}"

    @property
    def is_ended(self) -> bool:
        return self.ended_at is not None


class DeviceKey(models.Model):
    """The public half of a hardware key that signs a phone in without the password (ADR 0018).

    One per ``device_id``: a new key replaces the old one, also when another account held it. Only the
    public key (SPKI DER) is stored, so nothing on the server can sign in. It expires ``DEVICE_KEY_LIFETIME_DAYS``
    after its last use, and is deleted when the password or the email changes and with the account.
    The device fields are copied from the session that registered it, never taken from the request.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_keys"
    )
    device_id = models.UUIDField(unique=True)
    public_key = models.BinaryField()
    algorithm = models.CharField(max_length=5, default="ES256")
    platform = models.CharField(max_length=7, choices=Platform)
    device_name = models.CharField(max_length=100)
    # Whether the platform vouched for the key: an Android key with a verified attestation. iOS has none.
    attested = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "-created_at", "-id"], name="users_devicekey_by_user"
            ),
            models.Index(fields=["expires_at"], name="users_devicekey_expires"),
        ]

    def __str__(self) -> str:
        return f"{self.platform} device key of {self.user_id}"
