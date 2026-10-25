from django.urls import path

from . import views

urlpatterns = [
    path("accounts/", views.AccountList.as_view(), name="account-list"),
    path("accounts/<uuid:pk>/", views.AccountDetail.as_view(), name="account-detail"),
    path("categories/", views.CategoryList.as_view(), name="category-list"),
    path(
        "categories/<uuid:pk>/",
        views.CategoryDetail.as_view(),
        name="category-detail",
    ),
]
