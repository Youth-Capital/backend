from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts & Access"

    def ready(self) -> None:
        # Registers the OpenAPI security scheme for CookieJWTAuthentication.
        from .api import schema  # noqa: F401
