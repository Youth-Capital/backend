from django.apps import AppConfig


class MentorshipConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mentorship"
    label = "mentorship"
    verbose_name = "Mentorship"

    def ready(self) -> None:
        from . import signals  # noqa: F401
