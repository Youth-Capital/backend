"""Test settings — fast and hermetic."""

from .base import *  # noqa: F403
from .base import AUTHENTICATION_BACKENDS, env

DEBUG = False

# Run against PostgreSQL when TEST_DATABASE_URL is set — that is the only way
# to exercise the real constraints. Without it, fall back to SQLite so the
# suite still runs on a machine with no database server. Constraint behaviour
# differs between the two, so CI must supply the PostgreSQL URL.
_test_database_url = env("TEST_DATABASE_URL", default="")
if _test_database_url:
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(_test_database_url)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

# Fast hasher: Argon2 makes the suite an order of magnitude slower for no gain.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Lockouts would make auth tests order-dependent.
AXES_ENABLED = False
AUTHENTICATION_BACKENDS = [
    b for b in AUTHENTICATION_BACKENDS if "axes" not in b
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

RECOMPUTE_SYNCHRONOUS = True

# Throttling off, but the scope keys stay: a throttle class with an explicit
# scope raises ImproperlyConfigured if its key is missing, and a rate of None
# disables the limit without hiding a misconfiguration.
REST_FRAMEWORK = {  # noqa: F405
    **globals()["REST_FRAMEWORK"],
    "DEFAULT_THROTTLE_CLASSES": (),
    "DEFAULT_THROTTLE_RATES": {
        scope: None for scope in globals()["REST_FRAMEWORK"]["DEFAULT_THROTTLE_RATES"]
    },
}
