from django.utils import timezone
from factory.declarations import LazyFunction, Sequence
from factory.django import DjangoModelFactory, Password

from users.models import User


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User

    # factory-boy's declaration helpers carry no type hints, hence the ignores.
    email = Sequence(lambda n: f"person{n}@example.com")  # type: ignore[no-untyped-call]
    name = Sequence(lambda n: f"Test Person {n}")  # type: ignore[no-untyped-call]
    password = Password("valid-pass-1")  # type: ignore[no-untyped-call]


class VerifiedUserFactory(UserFactory):
    """A user whose email code was accepted, so the account can sign in."""

    email_verified_at = LazyFunction(timezone.now)  # type: ignore[no-untyped-call]
