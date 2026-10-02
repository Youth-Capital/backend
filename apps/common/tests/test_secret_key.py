"""The key that signs every session must never be one somebody can guess.

`SECRET_KEY` signs the JWTs, the password-reset tokens and the file download
links. The base settings used to fall back to `"dev-only-insecure-key-change-me"`
when the environment had none — a string that is in this repository, so on any
server started without the variable, anyone could mint a valid session for any
account. Production already demanded the variable; nothing stopped a server
from running the base or dev settings with DEBUG off, and nothing checked that
the value it was given was not a placeholder.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from apps.common.checks import check_secret_key

BACKEND_ROOT = Path(__file__).resolve().parents[3]

PRODUCTION_ENV = {
    "DJANGO_ALLOWED_HOSTS": "example.test",
    "EMAIL_HOST": "smtp.example.test",
    "EMAIL_HOST_USER": "mailer",
    "EMAIL_HOST_PASSWORD": "not-a-real-password",
    # Named so these tests fail over the key and nothing else. Production
    # refuses to start without a shared cache for the rate limits; that rule
    # has its own tests in apps/common/tests/test_cache_backend.py.
    "REDIS_URL": "redis://127.0.0.1:6379/0",
}


def _python(code: str, **env_overrides) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_overrides}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND_ROOT, env=env,
        capture_output=True, text=True, timeout=120,
    )


def test_the_base_settings_carry_no_usable_fallback_key():
    result = _python(
        "import config.settings.base as s; print(repr(s.SECRET_KEY))",
        DJANGO_SECRET_KEY="",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "''"


@pytest.mark.parametrize(
    "key",
    [
        "dev-only-insecure-key-change-me",
        "change-me",
        "django-insecure-" + "a" * 60,
        "short-but-random-Xq9",
    ],
)
def test_production_refuses_a_placeholder_or_weak_key(key):
    result = _python("import config.settings.prod", DJANGO_SECRET_KEY=key, **PRODUCTION_ENV)

    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_production_accepts_a_long_random_key():
    result = _python(
        "import config.settings.prod",
        DJANGO_SECRET_KEY="Vt3kQ9zL" * 8,
        **PRODUCTION_ENV,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("key", ["", "dev-only-insecure-key-change-me", "django-insecure-abc"])
def test_the_system_check_rejects_a_weak_key_outside_debug(key):
    with override_settings(DEBUG=False, SECRET_KEY=key):
        errors = check_secret_key(None)

    assert [error.id for error in errors] == ["security.E_SECRET_KEY"]


def test_a_developer_machine_may_run_on_its_dev_key():
    with override_settings(DEBUG=True, SECRET_KEY="django-insecure-dev-only"):
        assert check_secret_key(None) == []


def test_sessions_are_signed_with_the_key_actually_in_force():
    """Not with whatever the base module saw before an override."""
    from rest_framework_simplejwt.settings import api_settings

    assert api_settings.SIGNING_KEY == settings.SECRET_KEY
    assert api_settings.SIGNING_KEY
