"""The routes under ``/api/v1/``.

Each app adds its routes here as its endpoints are built, for example
``path("auth/", include("users.urls"))``. There are none yet.
"""

from django.urls import URLPattern, URLResolver

urlpatterns: list[URLPattern | URLResolver] = []
