"""Start-up checks for settings that must never reach a real deployment.

Registered with Django's system checks, so they run on every `manage.py`
command — `runserver` and `migrate` included — and a misconfigured server
stops with a named reason instead of running.
"""

from django.conf import settings
from django.core.checks import Error, Tags, register
from django.core.exceptions import ImproperlyConfigured

#: Values that have been in this repository at one time or another, and the
#: prefix Django itself puts on keys it generates for development.
KNOWN_PLACEHOLDERS = frozenset(
    {
        "dev-only-insecure-key-change-me",
        "change-me",
        "changeme",
        "secret",
        "django-insecure",
    }
)

MIN_LENGTH = 50


def is_weak_secret_key(key: str) -> bool:
    """Empty, short, a placeholder, or marked insecure by its own prefix."""
    key = key or ""
    return (
        len(key) < MIN_LENGTH
        or key.lower() in KNOWN_PLACEHOLDERS
        or key.startswith("django-insecure")
        or len(set(key)) < 8
    )


@register(Tags.security)
def check_secret_key(app_configs, **kwargs):
    """A deployment must sign sessions with a key nobody else has.

    `SECRET_KEY` signs every JWT, password-reset token and download link. The
    base settings used to fall back to a string that is in this repository, so
    a server started without the variable accepted sessions anybody could mint.
    A developer's machine (DEBUG on) may use a throwaway key; nothing else may.
    """
    if settings.DEBUG:
        return []

    try:
        key = settings.SECRET_KEY
    except ImproperlyConfigured:
        # Django refuses to hand out an empty key; for this check that is
        # simply the weakest key there is.
        key = ""

    if is_weak_secret_key(key):
        return [
            Error(
                "SECRET_KEY is missing, too short, or a known placeholder.",
                hint=(
                    f"Set DJANGO_SECRET_KEY to at least {MIN_LENGTH} random characters, "
                    "e.g. python -c \"import secrets; print(secrets.token_urlsafe(64))\""
                ),
                id="security.E_SECRET_KEY",
            )
        ]
    return []
