"""Production settings — hardened."""

from .base import *  # noqa: F403
from .base import BASE_DIR, MIDDLEWARE, env

DEBUG = False

# Fail fast: a production deploy must supply a real secret key.
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

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
