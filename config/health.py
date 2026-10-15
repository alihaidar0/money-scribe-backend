from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


class HealthCheckMiddleware:
    """Answer ``GET /healthz`` with 200 before any other middleware runs.

    Platforms (orchestrators, load balancers) probe the container by IP
    or an internal hostname over plain HTTP; running first means those probes are
    not rejected by ALLOWED_HOSTS or redirected to HTTPS. It checks that the
    process serves requests (liveness) — add a readiness view for dependencies.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path == "/healthz":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)
