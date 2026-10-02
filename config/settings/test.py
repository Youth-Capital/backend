"""Test settings — fast and hermetic."""

from .base import *  # noqa: F403
from .base import AUTHENTICATION_BACKENDS, env

DEBUG = False

# This is the test suite, and it says so out loud.
#
# The demo seeding commands refuse to run on anything that looks like a server
# (apps/common/seedguard.py), and "looks like a server" is mostly `DEBUG` being
# off — which is also true here. This flag is how the suite distinguishes
# itself, and it is set in this module and nowhere else: no environment
# variable can turn it on, so it cannot travel to a real deployment.
TESTING = True

# Tests never depend on a developer's .env for the signing key.
SECRET_KEY = "test-settings-signing-key-used-only-by-the-automated-test-suite-4f8a"

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

# Uploads go to a throwaway directory, never to the project's own media roots.
#
# Before this, every test that saved a file wrote it into backend/media or
# backend/private-media for real, and nothing removed it: 79 files had piled up
# in the private root from test runs alone, indistinguishable on disk from real
# uploads. One directory per test process, deleted when the process exits.
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

_UPLOAD_SCRATCH = Path(tempfile.mkdtemp(prefix="yk-test-uploads-"))
MEDIA_ROOT = _UPLOAD_SCRATCH / "media"
PRIVATE_MEDIA_ROOT = _UPLOAD_SCRATCH / "private-media"
atexit.register(shutil.rmtree, _UPLOAD_SCRATCH, ignore_errors=True)

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
    # Hermetic, like the signing key above: nothing stands in front of the
    # test suite, so the forwarded header is never believed here. A test that
    # needs a proxy says so itself with override_settings.
    "NUM_PROXIES": 0,
}

TRUSTED_PROXY_COUNT = 0
