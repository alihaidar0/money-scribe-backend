from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from . import limits
from .network import rate_limit_key


class ClientRateThrottle(SimpleRateThrottle):  # type: ignore[misc]
    """A limit per client address, whatever the account (the rates are in ``DEFAULT_THROTTLE_RATES``).

    It sits on top of the per-account limits of ``users.limits`` and shares their guarantees: the count
    is one atomic increment in a fixed window (DRF's own throttle reads and rewrites a list, which lets
    parallel requests through), and if the store is down the request is refused with ``503``. A
    throttled call answers ``429`` with ``Retry-After`` and the code ``throttled``.
    """

    retry_after = 1

    def get_cache_key(self, request: Request, view: APIView) -> str:
        ident = rate_limit_key(self.get_ident(request))
        return str(self.cache_format % {"scope": self.scope, "ident": ident})

    def allow_request(self, request: Request, view: APIView) -> bool:
        if self.rate is None:
            return True
        self.key = self.get_cache_key(request, view)
        count, self.retry_after = limits.hit(self.key, self.duration)
        return bool(count <= self.num_requests)

    def wait(self) -> float:
        return float(self.retry_after)


class SignInThrottle(ClientRateThrottle):
    scope = "sign_in"


class RegisterThrottle(ClientRateThrottle):
    scope = "register"


class ResendCodeThrottle(ClientRateThrottle):
    scope = "resend_code"


class VerifyEmailThrottle(ClientRateThrottle):
    scope = "verify_email"
