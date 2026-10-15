from typing import Any


class OwnerScopedMixin:
    """Restrict a view's queryset to the signed-in user's own rows.

    Every query on user data starts from the signed-in user, so another user's object is simply not
    found (``404``) and never reveals that it exists. Put the mixin first in the view's bases::

        class AccountViewSet(OwnerScopedMixin, ModelViewSet):
            queryset = Account.objects.all()

    ``owner_field`` names the field that points at the owner (``owner`` by default). Test every endpoint
    with ``config.tests.isolation.assert_hidden_from_other_users``.
    """

    owner_field = "owner"

    def get_owner(self) -> Any:
        """The value the owner field is compared with: the signed-in user."""
        return self.request.user  # type: ignore[attr-defined]

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()  # type: ignore[misc]
        return queryset.filter(**{self.owner_field: self.get_owner()})
