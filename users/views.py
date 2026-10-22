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
    CONFLICT_RESPONSE,
    FORBIDDEN_RESPONSE,
    RETRY_AFTER_HEADER,
    SERVICE_UNAVAILABLE_RESPONSE,
    UNAUTHORIZED_RESPONSE,
)
from config.authentication import PublicAuthentication
from config.error_codes import ErrorCode

from . import services
from .models import EndReason, Language, Session, User
from .network import client_ip
from .serializers import (
    ChangePasswordSerializer,
    CodeSentSerializer,
    DeleteAccountSerializer,
    EmailChangeConfirmSerializer,
    EmailChangeRequestSerializer,
    EmailChangeResponseSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifySerializer,
    ProfileSerializer,
    ProfileUpdateSerializer,
    RefreshResponseSerializer,
    RefreshSerializer,
    RegisterResponseSerializer,
    RegisterSerializer,
    ResendCodeSerializer,
    ResetTokenResponseSerializer,
    SignedInResponseSerializer,
    SignInSerializer,
    SignOutSerializer,
    UserSummarySerializer,
    VerifyEmailSerializer,
)
from .throttles import (
    AccountDeleteThrottle,
    EmailChangeConfirmThrottle,
    EmailChangeRequestThrottle,
    PasswordChangeThrottle,
    PasswordResetConfirmThrottle,
    PasswordResetRequestThrottle,
    PasswordResetVerifyThrottle,
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

    @extend_schema(
        operation_id="auth_profile_update",
        tags=TAG,
        summary="Edit the name",
        description=(
            "Changes the display name and answers the updated profile. Only `name` can be sent: any other "
            "field, such as `email`, `language` or `time_zone`, is refused with `400` and the code "
            "`read_only` on that field (the email changes only through `auth_email_change_request`). "
            "Errors: `400` `blank`, `required` or `max_length` on `name`."
        ),
        request=ProfileUpdateSerializer,
        responses={200: ProfileSerializer},
    )
    def patch(self, request: Request) -> Response:
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.change_name(
            user=request.user, name=serializer.validated_data["name"]
        )
        return Response(ProfileSerializer(user).data)


class PasswordResetRequestView(PublicAuthView):
    throttle_classes: ClassVar[list[Any]] = [PasswordResetRequestThrottle]

    @extend_schema(
        operation_id="auth_password_reset_request",
        tags=TAG,
        auth=[],
        summary="Ask for a password reset code",
        description=(
            "Step 1 of 3. Emails a 6-digit code to a registered, verified address. The answer is the "
            "same for every address, whether it has an account or not, so it reveals nothing; the app "
            "always goes on to the code screen. The code lives 10 minutes and shares the hourly budget "
            "of the other codes. Errors: `429` `resend_too_soon` (the 60 second cooldown), "
            "`resend_limit_reached` (5 codes per hour) or `throttled`, each with `Retry-After`; `503` "
            "`service_unavailable` while the limit store is down, with `Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=PasswordResetRequestSerializer,
        responses={200: CodeSentSerializer, 503: SERVICE_UNAVAILABLE_RESPONSE},
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sent = services.request_password_reset(email=serializer.validated_data["email"])
        return Response(
            {
                "code_expires_in": sent.code_expires_in,
                "resend_available_in": sent.resend_available_in,
            }
        )


class PasswordResetVerifyView(PublicAuthView):
    throttle_classes: ClassVar[list[Any]] = [PasswordResetVerifyThrottle]

    @extend_schema(
        operation_id="auth_password_reset_verify",
        tags=TAG,
        auth=[],
        summary="Check the password reset code",
        description=(
            "Step 2 of 3. A correct code is spent and answered with a single-use `reset_token` that "
            "lives 10 minutes. It does not sign anyone in. Errors: `400` `code_invalid` (the error item "
            "carries `attempts_left`) or `code_expired`, `429` `code_attempts_exceeded` (the code is "
            "dead; `Retry-After` is the wait before a new one may be requested) or `throttled`; `503` "
            "`service_unavailable` while the limit store is down, with `Retry-After`. The answers are "
            "the same for an address with no account."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=PasswordResetVerifySerializer,
        responses={
            200: ResetTokenResponseSerializer,
            503: SERVICE_UNAVAILABLE_RESPONSE,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        issued = services.verify_password_reset(email=data["email"], code=data["code"])
        return _token_response(
            {"reset_token": issued.reset_token, "expires_in": issued.expires_in}
        )


class PasswordResetConfirmView(PublicAuthView):
    throttle_classes: ClassVar[list[Any]] = [PasswordResetConfirmThrottle]

    @extend_schema(
        operation_id="auth_password_reset_confirm",
        tags=TAG,
        auth=[],
        summary="Set the new password",
        description=(
            "Step 3 of 3. Sets the new password with the `reset_token` and ends **all** the user's "
            "sessions, so the app returns to the sign-in screen (it signs nobody in). The owner gets an "
            "email about the change. Errors: `400` `reset_token_invalid` (`attr` `reset_token`: unknown, "
            "used or expired; ask for a new code), or a password rule on `attr` `password` "
            "(`password_too_short`, `password_too_long`, `password_too_similar`, `password_too_common`, "
            "`password_compromised`), after which the same token may be used again until it expires. "
            "A new password equal to the current one is allowed here: the reset proves the mailbox, not "
            "the old password. `429` `throttled` with `Retry-After`; `503` `service_unavailable` while the "
            "limit store is down, with `Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=PasswordResetConfirmSerializer,
        responses={204: None, 503: SERVICE_UNAVAILABLE_RESPONSE},
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.reset_password(
            reset_token=data["reset_token"], password=data["password"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangePasswordView(APIView):  # type: ignore[misc]
    throttle_classes: ClassVar[list[Any]] = [PasswordChangeThrottle]

    @extend_schema(
        operation_id="auth_password_change",
        tags=TAG,
        summary="Change the password",
        description=(
            "Needs the current password. Ends every **other** session; this one continues with the new "
            "token pair in the answer, and the previous refresh token is retired. The owner gets an "
            "email about the change. Errors: `400` `invalid_password` (`attr` `current_password`: it is "
            "`400`, not `401`, so the app's session handling is not triggered), `password_unchanged` "
            "(`attr` `password`: the new password equals the current one), or a password rule on `attr` "
            "`password` (`password_too_short`, `password_too_long`, `password_too_similar`, "
            "`password_too_common`, `password_compromised`); `429` `password_attempts_exceeded` (5 wrong "
            "current passwords lock this operation and the account deletion for 15 minutes; `Retry-After` "
            "is the remaining time; sign-in is unaffected) or `throttled`; `503` `service_unavailable` "
            "while the limit store is down, with `Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=ChangePasswordSerializer,
        responses={200: RefreshResponseSerializer, 503: SERVICE_UNAVAILABLE_RESPONSE},
    )
    def post(self, request: Request) -> Response:
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user: User = request.user
        session: Session = request.auth
        pair = services.change_password(
            user=user,
            session=session,
            current_password=data["current_password"],
            password=data["password"],
        )
        return _token_response(_pair_body(pair))


class DeleteAccountView(APIView):  # type: ignore[misc]
    throttle_classes: ClassVar[list[Any]] = [AccountDeleteThrottle]

    @extend_schema(
        operation_id="auth_account_delete",
        tags=TAG,
        summary="Delete the account",
        description=(
            "Needs the password. Deletes the account and **all** its data at once and for good, in one "
            "transaction, and every session ends with it (the tokens stop working at once). Nothing is "
            "kept: no soft delete and no anonymised copy. The address is used only for a confirmation "
            "email. It is `POST` with a body, not `DELETE`, because a `DELETE` body has no defined "
            "meaning and proxies may drop it. Errors: `400` `invalid_password` (`attr` `password`); "
            "`429` `password_attempts_exceeded` (shared with the password change) or `throttled`, "
            "each with `Retry-After`; `503` `service_unavailable` while the limit store is down, with "
            "`Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=DeleteAccountSerializer,
        responses={204: None, 503: SERVICE_UNAVAILABLE_RESPONSE},
    )
    def post(self, request: Request) -> Response:
        serializer = DeleteAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user: User = request.user
        services.delete_account(
            user=user, password=serializer.validated_data["password"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmailChangeView(APIView):  # type: ignore[misc]
    def get_throttles(self) -> list[Any]:
        # Cancelling needs no limit store, so it is not limited and can never answer a 503.
        if self.request.method == "POST":
            return [EmailChangeRequestThrottle()]
        return []

    @extend_schema(
        operation_id="auth_email_change_request",
        tags=TAG,
        summary="Start an email change",
        description=(
            "Step 1 of 2. Emails a 6-digit code to the **new** address; the old one stays in force until the "
            "code is accepted by `auth_email_change_confirm`. Needs the current password. A new request "
            "replaces the earlier pending change. Errors: `400` `invalid_password` (`attr` "
            "`current_password`), `email_unchanged` (`attr` `email`: the account already has it), or `invalid` "
            "for a malformed address; `409` `email_already_registered` (a signed-in caller who has proved the "
            "password may be told; no other call says so); `429` `password_attempts_exceeded` (the password "
            "lock shared with the password change), `resend_too_soon` (60 second cooldown for the new address), "
            "`resend_limit_reached` (5 codes per hour for the address, and 5 changes per hour for the "
            "account) or `throttled`, each with `Retry-After`; `503` `service_unavailable` while the limit "
            "store is down, with `Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=EmailChangeRequestSerializer,
        responses={
            200: EmailChangeResponseSerializer,
            409: CONFLICT_RESPONSE,
            503: SERVICE_UNAVAILABLE_RESPONSE,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = EmailChangeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sent = services.request_email_change(
            user=request.user,
            email=data["email"],
            current_password=data["current_password"],
        )
        return Response(
            {
                "email": sent.email,
                "code_expires_in": sent.code_expires_in,
                "resend_available_in": sent.resend_available_in,
            }
        )

    @extend_schema(
        operation_id="auth_email_change_cancel",
        tags=TAG,
        summary="Cancel the pending email change",
        description=(
            "Drops the pending change, so its code stops working. It answers `204` also when nothing is "
            "pending."
        ),
        responses={204: None},
    )
    def delete(self, request: Request) -> Response:
        services.cancel_email_change(user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmailChangeConfirmView(APIView):  # type: ignore[misc]
    throttle_classes: ClassVar[list[Any]] = [EmailChangeConfirmThrottle]

    @extend_schema(
        operation_id="auth_email_change_confirm",
        tags=TAG,
        summary="Confirm the email change",
        description=(
            "Step 2 of 2. A correct code switches the sign-in address to the new one, ends every **other** "
            "session (this one continues, with the same tokens), tells the old address by email and answers "
            "the updated profile. Errors: `400` `code_invalid` (the error item carries `attempts_left`) or "
            "`code_expired` (also when no change is pending); `409` `email_already_registered` (the address "
            "was taken meanwhile; the code is kept); `429` `code_attempts_exceeded` (the code is dead; "
            "`Retry-After` is the wait before a new one may be requested) or `throttled`; `503` "
            "`service_unavailable` while the limit store is down, with `Retry-After`."
        ),
        parameters=[RETRY_AFTER_HEADER],
        request=EmailChangeConfirmSerializer,
        responses={
            200: ProfileSerializer,
            409: CONFLICT_RESPONSE,
            503: SERVICE_UNAVAILABLE_RESPONSE,
        },
    )
    def post(self, request: Request) -> Response:
        serializer = EmailChangeConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.confirm_email_change(
            user=request.user,
            session=request.auth,
            code=serializer.validated_data["code"],
        )
        return Response(ProfileSerializer(user).data)
