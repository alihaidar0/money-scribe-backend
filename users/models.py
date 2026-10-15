import uuid
from typing import Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .managers import UserManager


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
    # Empty until the 4-digit email code is accepted; an unverified account cannot sign in.
    email_verified_at = models.DateTimeField(null=True, blank=True)
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
