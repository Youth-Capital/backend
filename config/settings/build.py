"""Image build settings: collect static assets without runtime credentials.

This module is never used by the running application. No database or provider
connection is needed to build Django admin's static assets.
"""

from .base import *  # noqa: F403

SECRET_KEY = "build-only-static-assets-key-7e0cb30bfc2a4866ac478fce19e82dab"
DEBUG = False
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
BILLING_MANUAL_AUTO_CONFIRM = False
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
WHITENOISE_KEEP_ONLY_HASHED_FILES = True
