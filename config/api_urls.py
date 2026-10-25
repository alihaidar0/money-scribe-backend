"""The routes under ``/api/v1/``.

Each app adds its routes here as its endpoints are built.
"""

from django.urls import URLPattern, URLResolver, include, path

urlpatterns: list[URLPattern | URLResolver] = [
    path("auth/", include("users.urls")),
    path("salary/", include("salary.urls")),
    path("", include("ledger.urls")),
    path("debts/", include("debts.urls")),
    path("reports/", include("reports.urls")),
    path("settings/", include("preferences.urls")),
]
