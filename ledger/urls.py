from django.urls import path

from . import views

urlpatterns = [
    path("accounts/", views.AccountList.as_view(), name="account-list"),
    path("accounts/<uuid:pk>/", views.AccountDetail.as_view(), name="account-detail"),
    path("transactions/", views.TransactionList.as_view(), name="transaction-list"),
    path(
        "transactions/<uuid:pk>/",
        views.TransactionDetail.as_view(),
        name="transaction-detail",
    ),
    path("categories/", views.CategoryList.as_view(), name="category-list"),
    path(
        "categories/<uuid:pk>/",
        views.CategoryDetail.as_view(),
        name="category-detail",
    ),
]
