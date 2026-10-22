from typing import Any, ClassVar

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authentication import get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import (
    FORBIDDEN_RESPONSE,
    RETRY_AFTER_HEADER,
    SERVICE_UNAVAILABLE_RESPONSE,
    UNAUTHORIZED_RESPONSE,
)
from config.authentication import PublicAuthentication
from config.error_codes import ErrorCode

from . import services
from .models import EndReason, Language
from .network import client_ip
from .serializers import (
    CodeSentSerializer,
    ProfileSerializer,
    RefreshResponseSerializer,
    RefreshSerializer,
    RegisterResponseSerializer,
    RegisterSerializer,
    ResendCodeSerializer,
    SignedInResponseSerializer,
    SignInSerializer,
    SignOutSerializer,
    UserSummarySerializer,
    VerifyEmailSerializer,
)
from .throttles import (
    RegisterThrottle,
    ResendCodeThrottle,
    SignInThrottle,
    VerifyEmailThrottle,
)

TAG = ["auth"]
TOKEN_TYPE = "Bearer"  # noqa: S105 - the scheme name, not a secret


class PublicAuthView(APIView):  # type: ignore[misc]
    """Base of the operations that need no access token."""

    authentication_classes: ClassVar[list[Any]] = [PublicAuthentication]
    permission_classes: ClassVar[list[Any]] = [AllowAny]


def _device(data: dict[str, Any]) -> services.DeviceInfo:
    return services.DeviceInfo(**data["device"])


def _client(request: Request) -> services.ClientInfo:
    return services.ClientInfo(
        ip=client_ip(request), user_agent=request.headers.get("user-agent", "")
    )


def _token_response(body: dict[str, Any]) -> Response:
    """An answer that carries tokens must never be cached (RFC 6749, section 5.1)."""
    return Response(body, headers={"Cache-Control": "no-store"})


def _pair_body(pair: services.TokenPair) -> dict[str, Any]:
    return {
        "access_token": pair.access_token,
        "refresh_token": pair.refresh_token,
        "token_type": TOKEN_TYPE,
        "expires_in": pair.expires_in,
    }


def _signed_in_body(signed_in: services.SignedIn) -> dict[str, Any]:
    return {
        **_pair_body(signed_in.tokens),
        "user": UserSummarySerializer(signed_in.user).data,
    }


class RegisterView(PublicAuthView):
    throttle_classes: ClassVar[list[Any]] = [RegisterThrottle]

    @extend_schema(
        operation_id="auth_register",
        tags=TAG,
        auth=[],
        summary="Register an account",
        description=(
            "Creates an unverified account and emails a 6-digit code. No session is created: the code is "
            "verified with `auth_verify_email`, which signs the user in. The answer is the same whether "
            "the address is new, pending or already registered, so it reveals nothing: a pending address "
            "is registered again (the new name and password replace the earlier ones), and the owner of "
            "a registered address gets an email saying someone tried, with no code. "
            "Errors: `400` field errors (password: `password_too_short`, `password_too_long`, "
            "`password_too_similar`, `password_too_common`, `password_compromised`; `invalid` for an "
            "email), `429` `resend_too_soon` or `resend_limit_reached` or `throttled`, each with "
            "`Retry-After`; `503` `service_unavailable` while the limit store is down, with `Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=RegisterSerializer,
        responses={201: RegisterResponseSerializer, 503: SERVICE_UNAVAILABLE_RESPONSE},
    )
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sent = services.register(
            name=data["name"],
            email=data["email"],
            password=data["password"],
            language=data.get("language", Language.ENGLISH),
            time_zone=data.get("time_zone", "UTC"),
        )
        return Response(
            {
                "email": sent.email,
                "code_expires_in": sent.code_expires_in,
                "resend_available_in": sent.resend_available_in,
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(PublicAuthView):
    throttle_classes: ClassVar[list[Any]] = [VerifyEmailThrottle]

    @extend_schema(
        operation_id="auth_verify_email",
        tags=TAG,
        auth=[],
        summary="Verify the email and sign in",
        description=(
            "A correct code verifies the address and starts the session, so the app goes straight in. "
            "Errors: `400` `code_invalid` (the error item carries `attempts_left`) or `code_expired`, "
            "`429` `code_attempts_exceeded` (the code is dead; `Retry-After` is the wait before a new one "
            "may be requested) or `throttled`; `503` `service_unavailable` while the limit store is "
            "down, with `Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=VerifyEmailSerializer,
        responses={200: SignedInResponseSerializer, 503: SERVICE_UNAVAILABLE_RESPONSE},
    )
    def post(self, request: Request) -> Response:
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        signed_in = services.verify_email(
            email=data["email"],
            code=data["code"],
            device=_device(data),
            client=_client(request),
            language=data.get("language"),
            time_zone=data.get("time_zone"),
        )
        return _token_response(_signed_in_body(signed_in))


class ResendCodeView(PublicAuthView):
    throttle_classes: ClassVar[list[Any]] = [ResendCodeThrottle]

    @extend_schema(
        operation_id="auth_resend_code",
        tags=TAG,
        auth=[],
        summary="Send a new code",
        description=(
            "Emails a new code, which replaces every earlier one. The answer is the same for every "
            "address, so it reveals nothing. Errors: `429` `resend_too_soon` (the 60 second cooldown), "
            "`resend_limit_reached` (5 codes per hour) or `throttled`, each with `Retry-After`; `503` "
            "`service_unavailable` while the limit store is down, with `Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=ResendCodeSerializer,
        responses={200: CodeSentSerializer, 503: SERVICE_UNAVAILABLE_RESPONSE},
    )
    def post(self, request: Request) -> Response:
        serializer = ResendCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sent = services.resend_code(email=serializer.validated_data["email"])
        return Response(
            {
                "code_expires_in": sent.code_expires_in,
                "resend_available_in": sent.resend_available_in,
            }
        )


class SignInView(PublicAuthView):
    throttle_classes: ClassVar[list[Any]] = [SignInThrottle]

    @extend_schema(
        operation_id="auth_sign_in",
        tags=TAG,
        auth=[],
        summary="Sign in",
        description=(
            "Starts a session. A client address that has failed several sign-ins is first asked for a "
            "bot challenge: `400` `captcha_required` (`attr` `captcha_token`) until it sends the token "
            "of a solved Turnstile widget (action `sign_in`) as `captcha_token`, `400` `captcha_invalid` "
            "for a token that is wrong, expired or used (it counts as a failed attempt); the token is "
            "single use, so a repeated attempt needs a new one. The email is looked up next. "
            "Errors: `401` `email_not_registered` "
            "(`attr` `email`: no account has it, whatever the password is, even blank), `401` "
            "`wrong_password` (`attr` `password`: the email has an account and the password is wrong), "
            "`401` `invalid_credentials` (any other failed sign-in, such as a deactivated account), `403` `email_not_verified` (only "
            "after the right password; the app opens the verify screen), `429` `sign_in_locked` (even "
            "the right password, while the account or the client address is locked; `Retry-After` is "
            "the remaining lock time) or `throttled`; `503` `service_unavailable` while the limit "
            "store is down, with `Retry-After`. Telling an unknown email from a wrong password lets "
            "anyone learn which emails have an account (ADR 0012); the limits are the brake, so a "
            "client must not retry automatically."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=SignInSerializer,
        responses={
            200: SignedInResponseSerializer,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            503: SERVICE_UNAVAILABLE_RESPONSE,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = SignInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        signed_in = services.sign_in(
            email=data["email"],
            password=data["password"],
            device=_device(data),
            client=_client(request),
            language=data.get("language"),
            time_zone=data.get("time_zone"),
            captcha_token=data.get("captcha_token", ""),
        )
        return _token_response(_signed_in_body(signed_in))


class RefreshView(PublicAuthView):
    @extend_schema(
        operation_id="auth_refresh",
        tags=TAG,
        auth=[],
        summary="Refresh the tokens",
        description=(
            "Trades the single-use refresh token for a new access token and a new refresh token. If the "
            "answer is lost, the same refresh token sent again within 30 seconds of the rotation returns "
            "the same new pair; an older used token ends the session. Errors: `401` `token_invalid`, "
            "`session_expired`, `session_signed_out`, `session_revoked_by_user`, `session_password_changed`, "
            "`session_email_changed` or `session_token_reuse`; all are final, so sign in again."
        ),
        request=RefreshSerializer,
        responses={200: RefreshResponseSerializer, 401: UNAUTHORIZED_RESPONSE},
    )
    def post(self, request: Request) -> Response:
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        pair = services.refresh(
            refresh_token=data["refresh_token"],
            device=_device(data),
            client=_client(request),
        )
        return _token_response(_pair_body(pair))


class SignOutView(PublicAuthView):
    """Authenticated by the access token, or by the refresh token in the body."""

    @extend_schema(
        operation_id="auth_sign_out",
        tags=TAG,
        # Optional bearer: the access token, or none when the refresh token is in the body. The stubs
        # type the argument as a list of names, but the library accepts requirement objects.
        auth=[{"bearerAuth": []}, {}],  # type: ignore[list-item]
        summary="Sign out",
        description=(
            "Ends the session at once. Send the bearer access token, or, when it has expired, the "
            "`refresh_token` in the body, so the device need not refresh first. Signing out a session that "
            "has already ended is not an error when the `refresh_token` is sent: it answers `204`, and so "
            "does a second call with the same `refresh_token`. Errors: `401`, and the `code` depends on "
            "what was sent. With the access token only: `token_expired` (refresh, or send the "
            "`refresh_token`), `token_invalid`, or the code of the session's end (`session_signed_out`, "
            "`session_revoked_by_user`, `session_password_changed`, `session_email_changed`, "
            "`session_token_reuse` or `session_expired`; the session is over, so the device is already "
            "signed out). With a `refresh_token` in the body: only `token_invalid`, when neither token is "
            "valid. It never answers `503`."
        ),
        request=SignOutSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        serializer = SignOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refresh_token = serializer.validated_data.get("refresh_token")
        parts = get_authorization_header(request).split()
        if len(parts) == 2 and parts[0].lower() == b"bearer":
            try:
                _, session = services.authenticate_access_token(
                    parts[1].decode(errors="replace"), client_ip=client_ip(request)
                )
            except AuthenticationFailed:
                if not refresh_token:
                    raise
            else:
                services.end_session(session, EndReason.SIGNED_OUT)
                return Response(status=status.HTTP_204_NO_CONTENT)
        if refresh_token:
            services.sign_out_with_refresh_token(refresh_token)
            return Response(status=status.HTTP_204_NO_CONTENT)
        raise AuthenticationFailed(
            detail="Send an access token or a refresh token.",
            code=ErrorCode.TOKEN_INVALID.value,
        )


class ProfileView(APIView):  # type: ignore[misc]
    """The signed-in user's own profile: there is no id in the path, so no other user's can be asked for."""

    @extend_schema(
        operation_id="auth_profile",
        tags=TAG,
        summary="Read the profile",
        description=(
            "The name and email of the signed-in user, and the `language` and `time_zone` stored from "
            "the device they last signed in on (read-only here). The app reads it after a restart, "
            "because the tokens carry no profile."
        ),
        responses={200: ProfileSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response(ProfileSerializer(request.user).data)
