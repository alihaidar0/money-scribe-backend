from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from .error_codes import ErrorCode


class BearerAuthentication(BaseAuthentication):  # type: ignore[misc]
    """Authenticate a request by its ``Authorization: Bearer <access token>`` header (ADR 0006).

    The token must verify **and** its session must be live, so ending a session cuts access at once.
    A request without a token is left unauthenticated (the default permission then answers ``401``);
    a bad one is refused with the code that says why. Every ``401`` carries ``WWW-Authenticate: Bearer``.
    ``request.auth`` is the ``users.Session``.
    """

    keyword = "Bearer"

    def authenticate(self, request: Request) -> tuple[object, object] | None:
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword.lower().encode():
            return None
        if len(parts) != 2:
            raise AuthenticationFailed(
                detail="The access token is not valid.",
                code=ErrorCode.TOKEN_INVALID.value,
            )
        # Imported here: the users app imports this module for its public authenticator.
        from users.network import client_ip
        from users.services import authenticate_access_token

        return authenticate_access_token(
            parts[1].decode(errors="replace"), client_ip=client_ip(request)
        )

    def authenticate_header(self, request: Request) -> str:
        return self.keyword


class PublicAuthentication(BaseAuthentication):  # type: ignore[misc]
    """For the operations that need no token (register, sign in, refresh, ...).

    It never reads the header, so a stale token sent by mistake cannot fail a sign-in. Its only job is
    ``authenticate_header``: without it DRF answers a ``401`` with a ``403``.
    """

    def authenticate(self, request: Request) -> None:
        return None

    def authenticate_header(self, request: Request) -> str:
        return BearerAuthentication.keyword


class BearerAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    """Declare the bearer scheme once in the OpenAPI document."""

    target_class = "config.authentication.BearerAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
