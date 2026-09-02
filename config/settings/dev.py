"""Local development settings."""

from .base import *  # noqa: F403
from .base import INSTALLED_APPS, env

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]

INSTALLED_APPS = INSTALLED_APPS + ["django_extensions"]

# Dev runs over plain http, so the refresh cookie cannot be Secure.
AUTH_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)

# Keep throttles generous while developing so the UI is not fighting 429s.
#
# Merged over the production rates rather than listed again. A fresh copy of
# the key set silently drops any scope added to base, and DRF raises on a
# scope with no rate — which is a 500 on every request to that view. That is
# exactly what happened when the refresh scope was introduced: sign-in worked,
# then every page load failed to restore the session.
REST_FRAMEWORK = {  # noqa: F405
    **globals()["REST_FRAMEWORK"],
    "DEFAULT_THROTTLE_RATES": {
        **globals()["REST_FRAMEWORK"]["DEFAULT_THROTTLE_RATES"],
        "anon": "200/min",
        "user": "2000/min",
        "auth": "30/min",
        "refresh": "600/min",
        "register": "100/hour",
        "candidate_search": "300/min",
        "ai": "300/hour",
    },
}
