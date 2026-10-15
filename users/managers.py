from typing import TYPE_CHECKING, Any

from django.contrib.auth.base_user import BaseUserManager

if TYPE_CHECKING:
    from .models import User


class UserManager(BaseUserManager["User"]):
    """Creates users that sign in with an email address instead of a username."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """Trim and lower-case the whole address, so one mailbox is one account."""
        return (email or "").strip().lower()

    def get_by_natural_key(self, username: str | None) -> User:
        return self.get(**{self.model.USERNAME_FIELD: self.normalize_email(username)})

    def _create_user(
        self, email: str, name: str, password: str | None, **extra_fields: Any
    ) -> User:
        email = self.normalize_email(email)
        if not email:
            raise ValueError("The email must be set.")
        name = name.strip()
        if not name:
            raise ValueError("The name must be set.")
        user = self.model(email=email, name=name, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(
        self, email: str, name: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, name, password, **extra_fields)

    def create_superuser(
        self, email: str, name: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["is_staff"] is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields["is_superuser"] is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create_user(email, name, password, **extra_fields)
