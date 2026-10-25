from django.urls import path

from . import views

urlpatterns = [
    path("", views.DebtList.as_view(), name="debt-list"),
    path("<uuid:pk>/", views.DebtDetail.as_view(), name="debt-detail"),
    path(
        "<uuid:pk>/repayments/",
        views.RepaymentCreate.as_view(),
        name="repayment-create",
    ),
    path(
        "<uuid:pk>/repayments/<uuid:repayment_id>/",
        views.RepaymentDetail.as_view(),
        name="repayment-detail",
    ),
]
