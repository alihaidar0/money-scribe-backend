from django.urls import path

from . import views

urlpatterns = [
    path("exports/", views.ExportView.as_view(), name="export-create"),
    path("imports/", views.ImportView.as_view(), name="import-create"),
]
