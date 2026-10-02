"""Production settings — hardened."""

from .base import *  # noqa: F403
from .base import BASE_DIR, MIDDLEWARE, REST_FRAMEWORK, env

DEBUG = False

# Fail fast: a production deploy must supply a real secret key, and a real one
# means long and random, not a placeholder that happens to be non-empty.
SECRET_KEY = env("DJANGO_SECRET_KEY")

from apps.common.checks import MIN_LENGTH, is_weak_secret_key  # noqa: E402

if is_weak_secret_key(SECRET_KEY):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "SECRET_KEY is too short or a known placeholder. Set DJANGO_SECRET_KEY to at "
        f"least {MIN_LENGTH} random characters."
    )
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

# Never in production, whatever the environment says. With this on, the manual
# payment provider settles every checkout without money moving, so one stray
# line in a server's .env would make every paid plan free. Refusing to start is
# the loud version of that mistake; apps/billing/checks.py covers any other
# settings module that runs with DEBUG off.
if env.bool("BILLING_MANUAL_AUTO_CONFIRM", default=False):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "BILLING_MANUAL_AUTO_CONFIRM must not be enabled in production: it confirms "
        "every manual payment without any money moving."
    )
BILLING_MANUAL_AUTO_CONFIRM = False

# --- The proxy in front of us -------------------------------------------
#
# One: the nginx described in deploy/nginx. Two if an external proxy is ever
# put in front of it — Cloudflare or a load balancer — because then nginx sees
# that proxy rather than the visitor.
#
# This is what makes the forwarded header worth reading at all. It is only
# sound in company with the other half of the arrangement: nginx overwrites
# `X-Forwarded-For` with the address it accepted the connection from, and
# gunicorn listens on the loopback interface, so no request can arrive with a
# header of its own choosing. Raising this number without that configuration
# in place would hand the choice of address back to the caller.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=1)

# Re-stated rather than inherited, because the dictionary in base.py captured
# the value it saw at import time. The same trap as SIMPLE_JWT's signing key:
# setting the number above and leaving the dictionary alone would leave the
# rate limits reading the development default of zero.
REST_FRAMEWORK = {**REST_FRAMEWORK, "NUM_PROXIES": TRUSTED_PROXY_COUNT}

# --- Rate limits have to be shared between workers ----------------------
#
# Refusing to start is deliberate. Without Redis the limits still appear to
# work — nothing errors, the tests pass, a burst of sign-in attempts is
# refused — while each gunicorn worker silently counts its own five per
# minute and forgets them on restart. A protection that looks present and
# is not is worse than an outage, because nobody goes looking for it.
# Read here rather than taken from base.py, and the cache built here too.
# base.py evaluated its own copy when *it* was first imported, which in a
# process that has already loaded another settings module is the wrong moment.
# Reading the environment at this module's import time is also what the rest
# of this file does for the secret key and the billing flag.
REDIS_URL = env("REDIS_URL", default="")

if not REDIS_URL:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "REDIS_URL is required in production. Rate limits are counted in the "
        "cache, so an in-process cache means every gunicorn worker keeps its "
        "own counters and a restart clears them. Set REDIS_URL to something "
        "like redis://127.0.0.1:6379/0 (rediss:// for TLS)."
    )

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "yc",
    }
}

# --- Transport security -------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# --- Cookies ------------------------------------------------------------
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False  # the SPA must read it to echo it back
CSRF_COOKIE_SAMESITE = "Lax"
AUTH_COOKIE_SECURE = True

# --- Headers ------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# --- Content Security Policy -------------------------------------------
MIDDLEWARE = [
    "csp.middleware.CSPMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE,
]

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'none'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "img-src": ["'self'", "data:", "https:"],
        "font-src": ["'self'"],
        "connect-src": ["'self'", *env.list("CORS_ALLOWED_ORIGINS", default=[])],
        # Lesson videos are framed from these two hosts and nowhere else.
        # `default-src: 'none'` covers frame-src as well, so without this line
        # every embedded lesson video is blocked — and only in production,
        # where this middleware runs. It would have looked perfect locally.
        #
        # The list is narrow on purpose: it is the second half of the guard in
        # apps/learning/video.py. That module will only ever build an address
        # on one of these hosts, and this says the browser will only load one.
        "frame-src": [
            "https://www.youtube-nocookie.com",
            "https://player.vimeo.com",
        ],
        # Us framing them, not them framing us. Still nobody.
        "frame-ancestors": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
    }
}

# --- Static -------------------------------------------------------------
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# --- Email --------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@yoshlarkapitali.uz")

# --- Error monitoring (opt-in) -----------------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        send_default_pii=False,  # never ship user PII to a third party
        environment=env("SENTRY_ENVIRONMENT", default="production"),
    )

# The handler opens its file the moment logging is configured, which is
# before anything else in Django runs. On a fresh machine that directory does
# not exist yet, and the process died at import with "Unable to configure
# handler 'file'" — the whole application refusing to boot over a missing
# folder. Creating it here costs nothing and removes a first-deploy failure.
LOG_DIR = BASE_DIR / "logs"  # noqa: F405
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING["handlers"]["file"] = {  # noqa: F405
    "class": "logging.handlers.RotatingFileHandler",
    "filename": str(LOG_DIR / "app.log"),
    "maxBytes": 10 * 1024 * 1024,
    "backupCount": 5,
    "formatter": "verbose",
    "filters": ["request_id"],
}
LOGGING["root"]["handlers"] = ["console", "file"]  # noqa: F405
