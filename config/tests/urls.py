"""A test-only URLconf with one small view for each behaviour the API foundation provides."""

from typing import Any, ClassVar

from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.generics import RetrieveUpdateDestroyAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api import OwnerScopedMixin
from config.api_errors import CONFLICT_RESPONSE
from config.error_codes import ErrorCode
from config.exceptions import conflict, throttled
from users.models import User

Ok = inline_serializer("Ok", {"ok": serializers.BooleanField()})


class WhoAmIView(APIView):  # type: ignore[misc]
    """Needs a signed-in user: the default permission."""

    @extend_schema(responses={200: Ok})
    def get(self, request: Request) -> Response:
        return Response({"ok": True, "id": str(request.user.pk)})


class PublicView(APIView):  # type: ignore[misc]
    authentication_classes: ClassVar[list[Any]] = []
    permission_classes: ClassVar[list[Any]] = [AllowAny]

    @extend_schema(responses={200: Ok})
    def get(self, request: Request) -> Response:
        return Response({"ok": True})


class ConflictView(APIView):  # type: ignore[misc]
    @extend_schema(responses={204: None, 409: CONFLICT_RESPONSE})
    def delete(self, request: Request) -> Response:
        raise conflict(
            ErrorCode.ACCOUNT_HAS_TRANSACTIONS, "The account has transactions."
        )


class LockedView(APIView):  # type: ignore[misc]
    authentication_classes: ClassVar[list[Any]] = []
    permission_classes: ClassVar[list[Any]] = [AllowAny]

    @extend_schema(responses={200: Ok})
    def post(self, request: Request) -> Response:
        raise throttled(ErrorCode.SIGN_IN_LOCKED, wait=42)


class InvalidView(APIView):  # type: ignore[misc]
    @extend_schema(
        request=inline_serializer("Body", {"name": serializers.CharField()}),
        responses={200: Ok},
    )
    def post(self, request: Request) -> Response:
        raise ValidationError({"name": ["This field is required."]})


class UserSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    class Meta:
        model = User
        fields: ClassVar[list[str]] = ["id"]


class OwnUserView(OwnerScopedMixin, RetrieveUpdateDestroyAPIView):  # type: ignore[misc]
    """A user row scoped by its own key: stands in for any model that has an owner field."""

    queryset = User.objects.all()
    serializer_class = UserSerializer
    owner_field = "pk"

    def get_owner(self) -> Any:
        return self.request.user.pk


api_patterns: list[URLPattern | URLResolver] = [
    path("whoami/", WhoAmIView.as_view()),
    path("public/", PublicView.as_view()),
    path("conflict/", ConflictView.as_view()),
    path("locked/", LockedView.as_view()),
    path("invalid/", InvalidView.as_view()),
    path("users/<uuid:pk>/", OwnUserView.as_view()),
]

urlpatterns = [
    path("api/v1/test/", include(api_patterns)),
]
