from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from .error_codes import ErrorCode


class BearerAuthentication(BaseAuthentication):  # type: ignore[misc]
    """Authenticate a request by its ``Authorization: Bearer <access token>`` header (ADR 0006).

    This is the seam the access-token check plugs into. Until the sign-in endpoints exist no token can be
    issued, so a request that carries a bearer token is refused with ``token_invalid``, and a request
    without one is left unauthenticated. Declaring the class already gives every ``401`` its
    ``WWW-Authenticate: Bearer`` header and the schema its bearer security scheme.
    """

    keyword = "Bearer"

    def authenticate(self, request: Request) -> tuple[object, object] | None:
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword.lower().encode():
            return None
        raise AuthenticationFailed(
            detail="The access token is not valid.", code=ErrorCode.TOKEN_INVALID.value
        )

    def authenticate_header(self, request: Request) -> str:
        return self.keyword


class BearerAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    """Declare the bearer scheme once in the OpenAPI document."""

    target_class = "config.authentication.BearerAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
