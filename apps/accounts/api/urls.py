from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .admin_views import AdminUserViewSet, EmployerVerificationViewSet
from .views import (
    ChangePasswordView,
    ConsentView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RefreshView,
    RegisterView,
    VerifyEmailView,
)

app_name = "accounts"

router = DefaultRouter()
router.register("admin/users", AdminUserViewSet, basename="admin-user")
router.register(
    "admin/verification", EmployerVerificationViewSet, basename="admin-verification"
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("consents/", ConsentView.as_view(), name="consents"),
    path("password/change/", ChangePasswordView.as_view(), name="password-change"),
    path("password/reset/", PasswordResetRequestView.as_view(), name="password-reset"),
    path(
        "password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("email/verify/", VerifyEmailView.as_view(), name="email-verify"),
    path("", include(router.urls)),
]
