from django.apps import AppConfig


class ExperienceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.experience"
    label = "experience"
    verbose_name = "Experience"

    def ready(self) -> None:
        from . import signals  # noqa: F401
