import os
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


class HealthCheckMiddleware:
    """Answer ``GET /healthz`` with 200 before any other middleware runs.

    Platforms (orchestrators, load balancers) probe the container by IP
    or an internal hostname over plain HTTP; running first means those probes are
    not rejected by ALLOWED_HOSTS or redirected to HTTPS. It checks that the
    process serves requests (liveness) — add a readiness view for dependencies.

    When the image was built with a commit (``APP_RELEASE``), the answer carries it in the ``X-Release`` header, so a
    deploy can wait until the new build is the one answering. The commit of a public repository is not a secret.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.release = os.environ.get("APP_RELEASE", "")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path == "/healthz":
            response = HttpResponse("ok", content_type="text/plain")
            response["Cache-Control"] = "no-store"
            if self.release:
                response["X-Release"] = self.release
            return response
        return self.get_response(request)
