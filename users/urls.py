from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("verify-email/", views.VerifyEmailView.as_view(), name="auth-verify-email"),
    path("resend-code/", views.ResendCodeView.as_view(), name="auth-resend-code"),
    path("sign-in/", views.SignInView.as_view(), name="auth-sign-in"),
    path("refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("sign-out/", views.SignOutView.as_view(), name="auth-sign-out"),
    path("profile/", views.ProfileView.as_view(), name="auth-profile"),
]
