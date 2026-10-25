from django.urls import path

from . import views
from .converters import register_month_converter

register_month_converter()

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
