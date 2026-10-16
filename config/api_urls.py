"""The routes under ``/api/v1/``.

Each app adds its routes here as its endpoints are built.
"""

from django.urls import URLPattern, URLResolver, include, path

urlpatterns: list[URLPattern | URLResolver] = [
    path("auth/", include("users.urls")),
]
