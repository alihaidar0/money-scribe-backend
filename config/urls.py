from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from .api_docs import docs_urlpatterns
from .readiness import readiness

admin.site.site_header = "Money Scribe administration"
admin.site.site_title = "Money Scribe"
admin.site.index_title = "Administration"

urlpatterns = [
    # The admin panel is for staff accounts only; its path comes from ADMIN_URL.
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", include("config.api_urls")),
    # Do the database and the cache answer? Token protected, off when the token is empty (config/readiness.py).
    path("readyz", readiness, name="readiness"),
    # Swagger UI and the live schema: staff only, and never in production (config/api_docs.py).
    *docs_urlpatterns(),
]
