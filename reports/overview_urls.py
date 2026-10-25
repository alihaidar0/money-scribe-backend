from django.urls import path

from salary.converters import register_month_converter

from . import views

register_month_converter()

urlpatterns = [
    path("months/<month:month>/", views.OverviewView.as_view(), name="overview-month"),
]
