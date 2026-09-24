from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"
    verbose_name = "Common"

    def ready(self) -> None:
        # Start-up refusals for settings that must never reach a deployment.
        from . import checks  # noqa: F401
