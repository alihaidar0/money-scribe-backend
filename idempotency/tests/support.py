"""A throwaway resource that wears every piece of the safe writes (ADR 0016), for the tests only.

The model has no migration: a fixture creates its table for the length of a test. The views are the ones a real
resource writes, built from the same mixins.
"""

import time
import uuid
from typing import Any, ClassVar

from django.db import models
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import VersionedModel
from config.api import IdempotentCreateMixin, OwnerScopedMixin, VersionedMixin
from config.api_errors import (
    CONFLICT_RESPONSE,
    ETAG_HEADER,
    IDEMPOTENCY_KEY_HEADER,
    IDEMPOTENT_REPLAYED_HEADER,
    IF_MATCH_HEADER,
    IF_NONE_MATCH_HEADER,
    PRECONDITION_FAILED_RESPONSE,
    UNPROCESSABLE_RESPONSE,
)
from config.preconditions import (
    check_create_only,
    check_if_match,
    check_if_match_on_create,
    with_etag,
)

# A title that makes a handler hold its transaction for a moment, so the tests can overlap two requests.
SLOW = "slow"
HOLD_SECONDS = 1.2


class Note(VersionedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    # A plain id, not a foreign key: this model has no real table outside a test, and a foreign key to the user
    # would make every account deletion in the suite look for it.
    owner_id = models.UUIDField()
    title = models.CharField(max_length=50)

    objects: ClassVar[models.Manager[Note]] = models.Manager()

    class Meta:
        app_label = "idempotency"


class NoteSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    class Meta:
        model = Note
        fields = ("id", "title", "version", "created_at", "updated_at")
        read_only_fields = ("id", "version", "created_at", "updated_at")


@extend_schema_view(
    post=extend_schema(
        operation_id="note_create",
        description="Creates a note once, however often the request is repeated.",
        parameters=[IDEMPOTENCY_KEY_HEADER, IDEMPOTENT_REPLAYED_HEADER, ETAG_HEADER],
        responses={
            201: NoteSerializer,
            409: CONFLICT_RESPONSE,
            422: UNPROCESSABLE_RESPONSE,
        },
    ),
    get=extend_schema(operation_id="note_list", description="The user's notes."),
)
class NoteList(OwnerScopedMixin, IdempotentCreateMixin, ListCreateAPIView):  # type: ignore[misc]
    owner_field = "owner_id"
    cursor_ordering = ("-title", "-id")
    queryset = Note.objects.all()
    serializer_class = NoteSerializer

    def get_owner(self) -> Any:
        return self.request.user.pk

    def perform_create(self, serializer: Any) -> None:
        if serializer.validated_data["title"] == SLOW:
            time.sleep(HOLD_SECONDS)
        serializer.save(owner_id=self.request.user.pk)


class AscendingNotes(OwnerScopedMixin, ListAPIView):  # type: ignore[misc]
    """The same notes in the other directions, to test every mix of ascending and descending fields."""

    owner_field = "owner_id"
    queryset = Note.objects.all()
    serializer_class = NoteSerializer
    cursor_ordering: ClassVar[tuple[str, ...]] = ("title", "id")

    def get_owner(self) -> Any:
        return self.request.user.pk


class MixedNotes(AscendingNotes):
    cursor_ordering = ("title", "-id")


class UnorderedNotes(AscendingNotes):
    cursor_ordering = ()


@extend_schema_view(
    get=extend_schema(
        operation_id="note_read",
        description="One note.",
        parameters=[ETAG_HEADER],
    ),
    put=extend_schema(
        operation_id="note_replace",
        description="Replaces a note.",
        parameters=[IF_MATCH_HEADER, ETAG_HEADER],
        responses={200: NoteSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
    patch=extend_schema(
        operation_id="note_update",
        description="Edits a note.",
        parameters=[IF_MATCH_HEADER, ETAG_HEADER],
        responses={200: NoteSerializer, 412: PRECONDITION_FAILED_RESPONSE},
    ),
    delete=extend_schema(
        operation_id="note_delete",
        description="Deletes a note.",
        parameters=[IF_MATCH_HEADER],
        responses={204: None, 412: PRECONDITION_FAILED_RESPONSE},
    ),
)
class NoteDetail(OwnerScopedMixin, VersionedMixin, RetrieveUpdateDestroyAPIView):  # type: ignore[misc]
    owner_field = "owner_id"
    queryset = Note.objects.all()
    serializer_class = NoteSerializer

    def get_owner(self) -> Any:
        return self.request.user.pk

    def perform_update(self, serializer: Any) -> None:
        if serializer.validated_data.get("title") == SLOW:
            time.sleep(HOLD_SECONDS)
        serializer.save()


class NoteByTitle(APIView):  # type: ignore[misc]
    """Create or replace a note under a title the client chose, like a salary month under ``YYYY-MM``."""

    @extend_schema(
        operation_id="note_put",
        description="Creates the note of this title, or replaces it.",
        request=NoteSerializer,
        parameters=[IF_MATCH_HEADER, IF_NONE_MATCH_HEADER, ETAG_HEADER],
        responses={
            200: NoteSerializer,
            201: NoteSerializer,
            412: PRECONDITION_FAILED_RESPONSE,
        },
    )
    def put(self, request: Request, title: str) -> Response:
        existing = Note.objects.filter(owner_id=request.user.pk, title=title).first()
        check_create_only(request, existing is not None)
        if existing is None:
            check_if_match_on_create(request)
            note = Note.objects.create(owner_id=request.user.pk, title=title)
            return with_etag(Response(NoteSerializer(note).data, status=201))
        check_if_match(request, existing.version)
        existing.save()
        return with_etag(Response(NoteSerializer(existing).data))


api_patterns: list[URLPattern | URLResolver] = [
    path("notes/", NoteList.as_view()),
    path("notes/<uuid:pk>/", NoteDetail.as_view()),
    path("notes-ascending/", AscendingNotes.as_view()),
    path("notes-mixed/", MixedNotes.as_view()),
    path("notes-unordered/", UnorderedNotes.as_view()),
    path("notes/by-title/<str:title>/", NoteByTitle.as_view()),
]
urlpatterns = [
    path(
        "api/v1/auth/", include("users.urls")
    ),  # the tests sign in through the real endpoint
    path("api/v1/", include(api_patterns)),
]
