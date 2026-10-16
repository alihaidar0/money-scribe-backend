"""The interactive API docs (Swagger UI) and the live schema, for staff outside production.

The committed ``openapi/schema.yaml`` is the contract. These two routes show the same schema from the running app so
that it can be read and tried in a browser:

* ``/api/docs/``: Swagger UI, bundled with the app (``drf-spectacular-sidecar``), nothing is loaded from a CDN. Use
  **Authorize** with an access token from ``auth/sign-in/`` to call the authenticated operations.
* ``/api/schema/``: the schema itself (YAML, or JSON with ``Accept: application/vnd.oai.openapi+json``).

They exist only when ``APP_ENVIRONMENT`` is not ``production`` (development and staging), and only for staff accounts:
an anonymous or non-staff visitor is sent to the admin sign-in. Production never serves them; its schema is the
``openapi.yaml`` attached to each release (ADR 0011).
"""

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import URLPattern, path
from django.views.decorators.cache import never_cache
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

SCHEMA_URL_NAME = "api-schema"


class StaffSchemaView(SpectacularAPIView):
    """The schema. Access is decided by the staff check around the view, not by DRF."""

    authentication_classes = ()
    permission_classes = (AllowAny,)


class StaffSwaggerView(SpectacularSwaggerView):
    """Swagger UI. Access is decided by the staff check around the view, not by DRF."""

    authentication_classes = ()
    permission_classes = (AllowAny,)


def docs_enabled() -> bool:
    """False in production, whatever else is set; staging and development get the docs."""
    return bool(settings.APP_ENVIRONMENT != "production")


def docs_urlpatterns() -> list[URLPattern]:
    """The two routes, or none in production. Never cached by a browser or a proxy."""
    if not docs_enabled():
        return []
    return [
        path(
            "api/schema/",
            never_cache(staff_member_required(StaffSchemaView.as_view())),
            name=SCHEMA_URL_NAME,
        ),
        path(
            "api/docs/",
            never_cache(
                staff_member_required(
                    StaffSwaggerView.as_view(url_name=SCHEMA_URL_NAME)
                )
            ),
            name="api-docs",
        ),
    ]
