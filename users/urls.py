from django.urls import path

from . import session_views, views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("verify-email/", views.VerifyEmailView.as_view(), name="auth-verify-email"),
    path("resend-code/", views.ResendCodeView.as_view(), name="auth-resend-code"),
    path("sign-in/", views.SignInView.as_view(), name="auth-sign-in"),
    path("refresh/", views.RefreshView.as_view(), name="auth-refresh"),
    path("sign-out/", views.SignOutView.as_view(), name="auth-sign-out"),
    path("profile/", views.ProfileView.as_view(), name="auth-profile"),
    path(
        "password-reset/request/",
        views.PasswordResetRequestView.as_view(),
        name="auth-password-reset-request",
    ),
    path(
        "password-reset/verify/",
        views.PasswordResetVerifyView.as_view(),
        name="auth-password-reset-verify",
    ),
    path(
        "password-reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path(
        "password/change/",
        views.ChangePasswordView.as_view(),
        name="auth-password-change",
    ),
    path("email-change/", views.EmailChangeView.as_view(), name="auth-email-change"),
    path(
        "email-change/confirm/",
        views.EmailChangeConfirmView.as_view(),
        name="auth-email-change-confirm",
    ),
    path(
        "account/delete/", views.DeleteAccountView.as_view(), name="auth-account-delete"
    ),
    path(
        "sessions/", session_views.SessionListView.as_view(), name="auth-session-list"
    ),
    path(
        "sessions/revoke-others/",
        session_views.RevokeOthersView.as_view(),
        name="auth-session-revoke-others",
    ),
    path(
        "sessions/<uuid:id>/",
        session_views.SessionDetailView.as_view(),
        name="auth-session-end",
    ),
    path(
        "session-history/",
        session_views.SessionHistoryView.as_view(),
        name="auth-session-history",
    ),
    path(
        "device-keys/",
        views.DeviceKeyListView.as_view(),
        name="auth-device-key-list",
    ),
    path(
        "device-keys/challenge/",
        views.DeviceKeyChallengeView.as_view(),
        name="auth-device-key-challenge",
    ),
    path(
        "device-keys/sign-in/",
        views.DeviceKeySignInView.as_view(),
        name="auth-device-key-sign-in",
    ),
    path(
        "device-keys/current/",
        views.DeviceKeyCurrentView.as_view(),
        name="auth-device-key-current",
    ),
    path(
        "device-keys/<uuid:device_id>/",
        views.DeviceKeyDetailView.as_view(),
        name="auth-device-key-detail",
    ),
]
