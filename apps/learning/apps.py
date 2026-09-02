from django.apps import AppConfig


class LearningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.learning"
    label = "learning"
    verbose_name = "Learning"

    def ready(self) -> None:
        from . import signals  # noqa: F401
