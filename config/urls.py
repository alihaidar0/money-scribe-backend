from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Money Scribe administration"
admin.site.site_title = "Money Scribe"
admin.site.index_title = "Administration"

urlpatterns = [
    # The admin panel is for staff accounts only; its path comes from ADMIN_URL.
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/v1/", include("config.api_urls")),
]
