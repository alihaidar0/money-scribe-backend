from factory.declarations import Sequence
from factory.django import DjangoModelFactory, Password

from users.models import User


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User

    # factory-boy's declaration helpers carry no type hints, hence the ignores.
    email = Sequence(lambda n: f"person{n}@example.com")  # type: ignore[no-untyped-call]
    name = Sequence(lambda n: f"Test Person {n}")  # type: ignore[no-untyped-call]
    password = Password("valid-pass-1")  # type: ignore[no-untyped-call]
