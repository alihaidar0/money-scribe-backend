from django.urls import path

from salary.converters import register_month_converter

from . import views

register_month_converter()

urlpatterns = [
    path("day-totals/", views.DayTotalsView.as_view(), name="report-day-totals"),
    path("months/<month:month>/", views.MonthReportView.as_view(), name="report-month"),
    path("debts/", views.DebtsReportView.as_view(), name="report-debts"),
]
