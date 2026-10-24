from django.urls import path, register_converter

from . import views
from .converters import MonthConverter

register_converter(MonthConverter, "month")

urlpatterns = [
    path("months/", views.SalaryMonthList.as_view(), name="salary-month-list"),
    path(
        "months/<month:month>/", views.SalaryMonthDetail.as_view(), name="salary-month"
    ),
    path(
        "months/<month:month>/defaults/",
        views.SalaryMonthDefaults.as_view(),
        name="salary-month-defaults",
    ),
    path(
        "months/<month:month>/fixed-items/",
        views.FixedItemCreate.as_view(),
        name="salary-fixed-item-create",
    ),
    path(
        "months/<month:month>/fixed-items/copy/",
        views.FixedItemsCopy.as_view(),
        name="salary-fixed-items-copy",
    ),
    path(
        "fixed-items/<uuid:pk>/",
        views.FixedItemDetail.as_view(),
        name="salary-fixed-item",
    ),
]
