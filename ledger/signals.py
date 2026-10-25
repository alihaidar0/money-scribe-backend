from typing import Any

from users.models import User

from . import services


def create_default_accounts(sender: Any, user: User, **kwargs: Any) -> None:
    """The receiver of ``users.signals.email_verified``: the person's first two accounts."""
    services.create_default_accounts(user)
