"""
Base settings shared by every environment.

Anything environment-specific (DEBUG, hosts, TLS, CORS origins) lives in
dev.py / prod.py / test.py. Secrets are never hardcoded — they come from the
environment via django-environ.
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BASE_DIR.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173"]),
    DATABASE_URL=(str, ""),
    ACCESS_TOKEN_LIFETIME_MINUTES=(int, 15),
    REFRESH_TOKEN_LIFETIME_DAYS=(int, 7),
    AUTH_COOKIE_SECURE=(bool, False),
    AUTH_COOKIE_SAMESITE=(str, "Lax"),
    AUTH_COOKIE_DOMAIN=(str, ""),
    AXES_FAILURE_LIMIT=(int, 5),
    AXES_COOLOFF_MINUTES=(int, 30),
    MINIMUM_AGE=(int, 14),
    AGE_OF_MAJORITY=(int, 18),
)

# Load backend/.env when present. Missing file is fine — the environment wins.
env_file = BASE_DIR / ".env"
if env_file.exists():
    env.read_env(str(env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key-change-me")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "axes",
]

LOCAL_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.audit",
    "apps.taxonomy",
    "apps.profiles",
    "apps.knowledge",
    "apps.capital",
    "apps.idp",
    "apps.learning",
    "apps.assessment",
    "apps.experience",
    "apps.cv",
    "apps.jobs",
    "apps.matching",
    "apps.ai",
    "apps.notifications",
    "apps.analytics",
    "apps.billing",
    "apps.feedback",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "apps.common.middleware.RequestIDMiddleware",
    "apps.audit.middleware.AuditContextMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
_database_url = env("DATABASE_URL")
if _database_url:
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.parse(
            _database_url,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=env.bool("DATABASE_SSL_REQUIRE", default=True),
        )
    }
else:
    # No DATABASE_URL yet: keep `makemigrations`, `check` and imports working.
    # Any command that actually touches the database will fail loudly, which is
    # the intent — we never silently fall back to a different engine.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", default="yoshlar_kapitali"),
            "USER": env("POSTGRES_USER", default="postgres"),
            "PASSWORD": env("POSTGRES_PASSWORD", default=""),
            "HOST": env("POSTGRES_HOST", default="localhost"),
            "PORT": env("POSTGRES_PORT", default="5432"),
            "CONN_MAX_AGE": 600,
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    # AxesStandaloneBackend must come first so lockouts are enforced before
    # the password is ever checked.
    "axes.backends.AxesStandaloneBackend",
    "apps.accounts.backends.EmailOrPhoneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Argon2id first — resistant to GPU cracking, unlike PBKDF2.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# django-axes — brute force protection
# --------------------------------------------------------------------------
AXES_FAILURE_LIMIT = env("AXES_FAILURE_LIMIT")
AXES_COOLOFF_TIME = timedelta(minutes=env("AXES_COOLOFF_MINUTES"))
AXES_LOCKOUT_PARAMETERS = [["ip_address", "username"]]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ADMIN = True
AXES_LOCKOUT_CALLABLE = "apps.accounts.lockout.lockout_response"

# --------------------------------------------------------------------------
# REST framework
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.accounts.authentication.CookieJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "300/min",
        # Sign-in and password change: tight, because guessing is the attack.
        "auth": "5/min",
        # Refreshing is not guessing. Every full page load spends one, so six
        # reloads in a minute used to trip the sign-in limit and end the
        # session. Replay is already handled by rotation plus blacklisting,
        # so this rate is a flood stop, not the defence.
        "refresh": "60/min",
        "register": "10/hour",
        "candidate_search": "60/min",
        "ai": "30/hour",
    },
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env("ACCESS_TOKEN_LIFETIME_MINUTES")),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env("REFRESH_TOKEN_LIFETIME_DAYS")),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_TYPE_CLAIM": "token_type",
}

# The refresh token never touches JavaScript: it lives in an httpOnly cookie.
AUTH_COOKIE_NAME = "yk_refresh"
AUTH_COOKIE_SECURE = env("AUTH_COOKIE_SECURE")
AUTH_COOKIE_SAMESITE = env("AUTH_COOKIE_SAMESITE")
AUTH_COOKIE_DOMAIN = env("AUTH_COOKIE_DOMAIN") or None
AUTH_COOKIE_PATH = "/api/v1/auth/"

#: What the product is called, wherever the backend has to say it out loud:
#: the admin, the API docs, an email subject, and the provider shown against
#: content the platform published itself.
#:
#: One place because it has already been renamed once, and the name had been
#: copied into half a dozen files — one of which compared against it to decide
#: whether a test belonged to the platform. Anything that needs to *test* for
#: platform ownership uses `is_platform`, never this string.
PLATFORM_NAME = "Youth Capital"

SPECTACULAR_SETTINGS = {
    "TITLE": f"{PLATFORM_NAME} API",
    "DESCRIPTION": (
        "Youth Capital — human capital platform. "
        "Education, diagnostics, individual development plans, Edu-Job matching."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "ENUM_NAME_OVERRIDES": {
        "ModerationStatusEnum": "apps.common.enums.ModerationStatus.choices",
    },
}

# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True  # required for the refresh cookie
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# --------------------------------------------------------------------------
# Internationalization — UZ (latin) is the default per TZ §12
# --------------------------------------------------------------------------
LANGUAGE_CODE = "uz"
LANGUAGES = [("uz", "O'zbekcha"), ("ru", "Русский"), ("en", "English")]
LANGUAGE_FALLBACK_ORDER = ["uz", "ru", "en"]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static & media
# --------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
ALLOWED_UPLOAD_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"]
ALLOWED_UPLOAD_DOC_TYPES = ["application/pdf"]
MAX_UPLOAD_SIZE_MB = 5

# Books get their own two settings, because they are not worksheets.
#
# EPUB is here because half of what an author will have on disk is an EPUB,
# and refusing it would send them off to convert a file before they could
# attach it. The size cap is separate for the blunt reason that 5 MB is not a
# book: a scanned textbook clears that before its table of contents. 40 MB
# covers a typical text-and-diagrams PDF without turning the endpoint into
# somewhere to park a video.
ALLOWED_UPLOAD_BOOK_TYPES = ["application/pdf", "application/epub+zip"]
MAX_BOOK_SIZE_MB = 40

# The ceiling on decoded image size, in megapixels.
#
# File size is not the limit that matters here: a single-colour PNG of 20000 x
# 20000 compresses to about a megabyte and decodes to 1.2 GB of memory. Pillow
# carries its own default, but it is a library default that can change under
# us — this is the product's own rule, and it is enforced in the validator so
# an oversized picture is a rejected upload rather than a dead worker.
#
# 40 MP is generous for what this platform stores: avatars, course covers and
# portfolio shots. A 24-megapixel camera photo passes with room to spare.
MAX_IMAGE_MEGAPIXELS = 40

# --------------------------------------------------------------------------
# Domain configuration
# --------------------------------------------------------------------------
MINIMUM_AGE = env("MINIMUM_AGE")
AGE_OF_MAJORITY = env("AGE_OF_MAJORITY")

# AI provider: RULE_BASED needs no network access and no API key.
AI_DEFAULT_PROVIDER = env("AI_DEFAULT_PROVIDER", default="RULE_BASED")
AI_LOG_PROMPT_CONTENT = env.bool("AI_LOG_PROMPT_CONTENT", default=False)

# Recompute knowledge/capital/match synchronously when no broker is configured.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="")
RECOMPUTE_SYNCHRONOUS = not CELERY_BROKER_URL

FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173")

# --------------------------------------------------------------------------
# Logging — structured, with a request id on every line
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "apps.common.logging.RequestIDFilter"},
    },
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {request_id} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": True},
        "apps": {"level": "INFO", "propagate": True},
    },
}


# ---------------------------------------------------------------- billing
# Which gateway settles money. "manual" needs no credentials and is what the
# demo runs on; swap to "stripe" (or a dotted path to a local gateway) once
# keys exist. Never hardcode a key here — these read from the environment.
BILLING_PROVIDER = env("BILLING_PROVIDER", default="manual")

# Lets the manual provider settle *paid* plans in-process. Intended for demo
# and offline bank-transfer flows; defaults to DEBUG so production does not
# silently hand out paid tiers for free.
BILLING_MANUAL_AUTO_CONFIRM = env.bool("BILLING_MANUAL_AUTO_CONFIRM", default=DEBUG)

STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")
