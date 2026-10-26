"""The person's sessions and security activity (ADR 0009, ADR 0022): the lists, ending sessions, the timeline."""

import uuid
from datetime import timedelta
from typing import Any, ClassVar

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import (
    RETRY_AFTER_HEADER,
    SERVICE_UNAVAILABLE_RESPONSE,
)
from config.throttling import SessionActionThrottle, SessionBulkActionThrottle

from . import session_actions, sessions
from .models import SecurityEvent, Session
from .serializers import (
    RevokeOthersResultSerializer,
    RevokeOthersSerializer,
    SecurityEventSerializer,
    SessionListQuerySerializer,
    SessionSerializer,
)

TAG = ["sessions"]


class SessionListView(ListAPIView):  # type: ignore[misc]
    """The live sessions (the whole list, at most 10) or the ended ones of the last 90 days (in pages)."""

    serializer_class = SessionSerializer
    cursor_ordering = ("-created_at", "-id")

    def get_serializer_context(self) -> dict[str, Any]:
        context: dict[str, Any] = super().get_serializer_context()
        session: Session | None = getattr(self.request, "auth", None)
        context["current_id"] = session.id if session is not None else None
        return context

    def _status(self) -> str:
        query = SessionListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return str(query.validated_data["status"])

    def get_queryset(self) -> QuerySet[Session]:
        return sessions.ended_sessions(self.request.user, timezone.now())

    @extend_schema(
        operation_id="auth_session_list",
        tags=TAG,
        summary="The sessions of the account",
        description=(
            "`status=active` (the default) is the live sessions: the current one first, then the most recently "
            "active, at most 10, always the whole list with `next` and `previous` `null` (`limit` and `cursor` are "
            "accepted and ignored). A session is live when it has not been ended and its refresh token has not "
            "expired. `status=ended` is the sessions that ended in the last 90 days (never older, even before the "
            "daily purge has run), newest sign-in first, in pages (`limit` 50 by default, 100 at most). Every sign-in "
            "is a session, so a device that signed in five times has five rows with the same `device_id`; the app "
            "groups them. A session nobody used for 30 days counts as ended with `end_reason` `expired` and "
            "`ended_at` the moment its refresh token ran out; nothing is written for that. `limit` that is not a whole "
            "number of at least 1 is `400` `invalid`; a `status` that is not one of the two is `400` `invalid_choice`."
        ),
        parameters=[SessionListQuerySerializer],
        responses={200: SessionSerializer},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if self._status() == "ended":
            return super().list(request, *args, **kwargs)
        current = request.auth
        live = list(sessions.live_sessions(request.user, timezone.now()))
        # The current one first, then the most recently active; the larger id breaks a tie (as in the ended list).
        live.sort(
            key=lambda s: (s.id == current.id, s.last_active_at, s.id), reverse=True
        )
        body = SessionSerializer(
            live, many=True, context=self.get_serializer_context()
        ).data
        return Response({"next": None, "previous": None, "results": body})


class SessionDetailView(APIView):  # type: ignore[misc]
    throttle_classes: ClassVar[list[Any]] = [SessionActionThrottle]

    @extend_schema(
        operation_id="auth_session_end",
        tags=TAG,
        summary="End a session",
        description=(
            "Ends one of the person's own sessions (`204`). A session that is already over is also `204` and nothing "
            "changes. An id that is not theirs, or does not exist, is `404`. Ending the **current** session is a "
            "sign-out (the fingerprint key stays; the app uses the sign-out call for this). Ending **another** "
            "session makes that device answer `401` `session_revoked_by_user` on its next call, records a "
            "`session_revoked` entry in the security activity, and removes the fingerprint key of that session's "
            "device (unless it is the current device's) in the same transaction; there is no opt-out and the answer "
            "has no body. It counts in the session actions rate group (30 an hour) and keeps working while the limit "
            "store is down, because it only reduces access."
        ),
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="The session id.",
            )
        ],
        responses={204: None},
    )
    def delete(
        self, request: Request, id: uuid.UUID
    ) -> Response:  # the URL's name for it
        session_actions.end_one(user=request.user, current=request.auth, session_id=id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RevokeOthersView(APIView):  # type: ignore[misc]
    throttle_classes: ClassVar[list[Any]] = [SessionBulkActionThrottle]

    @extend_schema(
        operation_id="auth_session_revoke_others",
        tags=TAG,
        summary="End every other session",
        description=(
            "Needs the password. Ends every session but the current one and removes every fingerprint key but the "
            "current device's, the keys of signed-out phones included, in one transaction. Answers `200` with how many "
            "of each; with no other sessions `ended` is 0 and the keys of other devices are still removed. A wrong "
            "password is `400` `invalid_password` on `current_password`; too many wrong ones are `429` "
            "`password_attempts_exceeded` (the lock shared with the password change); a field that is not "
            "`current_password` is `400` `read_only`. It counts in the session actions rate group (30 an hour) and "
            "fails closed: `503` `service_unavailable` while the limit store is down."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=RevokeOthersSerializer,
        responses={
            200: RevokeOthersResultSerializer,
            503: SERVICE_UNAVAILABLE_RESPONSE,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = RevokeOthersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        done = session_actions.end_others(
            user=request.user,
            current=request.auth,
            password=serializer.validated_data["current_password"],
        )
        return Response(
            RevokeOthersResultSerializer(
                {"ended": done.ended, "device_keys_removed": done.device_keys_removed}
            ).data
        )


class SessionHistoryView(ListAPIView):  # type: ignore[misc]
    serializer_class = SecurityEventSerializer
    cursor_ordering = ("-occurred_at", "-id")

    def get_queryset(self) -> QuerySet[SecurityEvent]:
        cutoff = timezone.now() - timedelta(days=settings.SECURITY_EVENT_RETENTION_DAYS)
        return SecurityEvent.objects.filter(
            user=self.request.user, occurred_at__gte=cutoff
        )

    @extend_schema(
        operation_id="auth_session_history",
        tags=TAG,
        summary="The security activity of the account",
        description=(
            "What happened to the account, newest first (`occurred_at`, `id`), in pages (`limit` 50 by default, 100 at "
            "most). Entries are kept 90 days; an older one is never returned, even before the purge has run. The "
            "types are `signed_in`, `signed_out`, `session_revoked`, `session_limit_reached`, `password_changed`, "
            "`password_reset`, `email_changed`, `device_key_added`, `device_key_removed`, `token_reuse_detected`, "
            "`data_exported` and `data_imported`; a client treats an unknown type, platform or sign-in method as an "
            "open value. **Which device an entry carries:** for `signed_in`, `signed_out`, `password_changed`, "
            "`email_changed`, `data_exported` and `data_imported` the device where the action was done; for "
            "`session_revoked`, `session_limit_reached` and `token_reuse_detected` the device of the session that was "
            "ended (`session_id` is that session); for `device_key_added` and `device_key_removed` the device of the "
            "key (`app_version` is `null` when the acting session is on another device); `password_reset` has no "
            "session, so its device fields are `null`. **Imports of long files:** an entry is never edited or merged "
            "on the server, and an import writes one `data_imported` entry for each call that writes at least one row "
            "(at most 500 rows), so a file of 10 000 rows is up to 20 entries; the app may show consecutive entries "
            "of the same `session_id`, less than 15 minutes apart, as one. No IP address, user agent, amount, note or "
            "name is in an entry. Failed sign-ins and the deletion of the account are not entries."
        ),
        responses={200: SecurityEventSerializer},
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)
