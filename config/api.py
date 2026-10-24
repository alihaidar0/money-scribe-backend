from collections.abc import Callable
from typing import Any

from django.db import transaction
from rest_framework.request import Request
from rest_framework.response import Response

from idempotency import services as idempotency

from .preconditions import check_if_match, with_etag


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


class IdempotentCreateMixin:
    """Make the ``POST`` that creates a record safe to repeat (ADR 0016).

    The request must carry ``Idempotency-Key`` (``400`` without it). The first success is stored with the record,
    in one transaction; the same key with the same request returns that answer again with ``Idempotent-Replayed:
    true``, and with another request answers ``422 idempotency_key_reused``. Put it before the view's create
    mixin::

        class TransactionList(OwnerScopedMixin, IdempotentCreateMixin, ListCreateAPIView): ...

    The answer carries the ``ETag`` of the new record when its body has a ``version``.
    """

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        create: Callable[..., Response] = super().create  # type: ignore[misc]
        answer: Response = idempotency.run(
            request, lambda: with_etag(create(request, *args, **kwargs))
        )
        return answer


class VersionedMixin:
    """Version checks for a ``RetrieveUpdateDestroyAPIView`` over a ``common.models.VersionedModel`` (ADR 0016).

    Reading answers the record's ``ETag``. An edit or a delete is made under a row lock: ``If-Match`` is compared
    with the stored ``version`` and, when it is not that one, nothing changes and the answer is
    ``412 precondition_failed``; two saves at once therefore cannot both win. Without ``If-Match`` the last write
    wins. The answer to an edit carries the new ``ETag``.
    """

    _lock_rows = False

    def get_queryset(self) -> Any:
        queryset = super().get_queryset()  # type: ignore[misc]
        return queryset.select_for_update(of=("self",)) if self._lock_rows else queryset

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        retrieve: Callable[..., Response] = super().retrieve  # type: ignore[misc]
        return with_etag(retrieve(request, *args, **kwargs))

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        view: Any = self  # the generic view this mixin is combined with; DRF ships no type hints
        partial = kwargs.pop("partial", False)
        with transaction.atomic():
            self._lock_rows = True
            instance = view.get_object()
            check_if_match(request, instance.version)
            serializer = view.get_serializer(
                instance, data=request.data, partial=partial
            )
            serializer.is_valid(raise_exception=True)
            view.perform_update(serializer)
        return with_etag(Response(serializer.data))

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        view: Any = self
        with transaction.atomic():
            self._lock_rows = True
            instance = view.get_object()
            check_if_match(request, instance.version)
            view.perform_destroy(instance)
        return Response(status=204)
