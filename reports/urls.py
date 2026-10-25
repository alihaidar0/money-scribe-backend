from django.urls import path

from . import views

urlpatterns = [
    path("day-totals/", views.DayTotalsView.as_view(), name="report-day-totals"),
]
