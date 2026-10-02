"""Rate limits are counted in the cache, so the cache decides whether they hold.

DRF keeps every throttle counter in Django's default cache, and nothing else in
this project uses that cache. With the in-memory backend and gunicorn's four
workers, "five sign-in attempts per minute" was four separate counts of five,
forgotten on every restart — a limit that looked present in every test and in
every manual check, and was four times looser than it read.

So production refuses to start without `REDIS_URL`, and a developer's machine
and this suite keep the in-memory cache and need nothing installed or running.

On the cross-process tests below: there is no Redis server on a development
machine, and these tests must not need one. What they establish is the
property the choice rests on — that an in-process cache cannot be shared
between processes and a cache outside the process can — using Django's
file-based backend as the stand-in for Redis. That the production backend is
one of the shareable kind is asserted separately, from the settings.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

BACKEND_ROOT = Path(__file__).resolve().parents[3]

#: The least a production settings import needs, with obviously fake values.
PRODUCTION_ENV = {
    "DJANGO_SECRET_KEY": "test-only-fake-signing-key-9f3Kq2Lm8Vx4Rt7Zp1Nb6Hc5Wd0Ys3Ju",
    "DJANGO_ALLOWED_HOSTS": "example.test",
    "EMAIL_HOST": "smtp.example.test",
    "EMAIL_HOST_USER": "mailer",
    "EMAIL_HOST_PASSWORD": "not-a-real-password",
}

LOCAL_MEMORY = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "shared-in-name-only",
    }
}


def _settings_module(module: str, code: str, **env_overrides):
    """Import a settings module in a fresh process and print something from it."""
    env = {**os.environ, **PRODUCTION_ENV, **env_overrides}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    return subprocess.run(
        [sys.executable, "-c", f"import {module} as s\n{code}"],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )


def _cache_process(action: str, caches_config: dict) -> subprocess.CompletedProcess:
    """One separate process, standing in for one gunicorn worker."""
    code = (
        "import json, sys\n"
        "from django.conf import settings\n"
        f"settings.configure(CACHES=json.loads({json.dumps(json.dumps(caches_config))}), USE_TZ=True)\n"
        "from django.core.cache import cache\n"
        "if sys.argv[1] == 'count':\n"
        "    cache.set('throttle_auth_203.0.113.7', [1, 2, 3], 60)\n"
        "    print('counted')\n"
        "else:\n"
        "    print(repr(cache.get('throttle_auth_203.0.113.7')))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code, action],
        cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=120,
    )


# ---------------------------------------------------------------------------
# What a per-process cache means for a rate limit
# ---------------------------------------------------------------------------
def test_an_in_process_cache_is_not_shared_between_workers():
    """The bug, demonstrated: worker two never sees worker one's count."""
    written = _cache_process("count", LOCAL_MEMORY)
    assert written.stdout.strip() == "counted", written.stderr

    read = _cache_process("read", LOCAL_MEMORY)

    assert read.stdout.strip() == "None", read.stderr


def test_a_cache_outside_the_process_is_shared_between_workers(tmp_path):
    """And the fix: a store outside the process is one store for all of them.

    Django's file-based backend stands in for Redis here so that this test
    needs no server. Redis is shared for the same reason and in the same way:
    the counter does not live inside the worker.
    """
    shared = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": str(tmp_path / "shared-cache"),
        }
    }
    written = _cache_process("count", shared)
    assert written.stdout.strip() == "counted", written.stderr

    read = _cache_process("read", shared)

    assert read.stdout.strip() == "[1, 2, 3]", read.stderr


# ---------------------------------------------------------------------------
# Production insists on it
# ---------------------------------------------------------------------------
def test_production_refuses_to_start_without_redis():
    result = _settings_module("config.settings.prod", "print(s.CACHES)", REDIS_URL="")

    assert result.returncode != 0
    assert "REDIS_URL" in result.stderr


def test_production_uses_redis_when_it_is_named():
    result = _settings_module(
        "config.settings.prod",
        "print(s.CACHES['default']['BACKEND']); print(s.CACHES['default']['LOCATION'])",
        REDIS_URL="redis://127.0.0.1:6379/2",
    )

    assert result.returncode == 0, result.stderr
    backend, location = result.stdout.split()
    assert backend == "django.core.cache.backends.redis.RedisCache"
    assert location == "redis://127.0.0.1:6379/2"


def test_the_production_cache_is_never_an_in_process_one():
    """Whatever else changes, the backend must be one workers can share."""
    result = _settings_module(
        "config.settings.prod",
        "print(s.CACHES['default']['BACKEND'])",
        REDIS_URL="redis://127.0.0.1:6379/0",
    )

    assert result.returncode == 0, result.stderr
    assert "locmem" not in result.stdout
    assert "dummy" not in result.stdout


# ---------------------------------------------------------------------------
# Development and the test suite do not
# ---------------------------------------------------------------------------
def test_development_runs_without_redis():
    result = _settings_module(
        "config.settings.dev", "print(s.CACHES['default']['BACKEND'])", REDIS_URL=""
    )

    assert result.returncode == 0, result.stderr
    assert "locmem" in result.stdout


def test_development_can_opt_in_to_redis():
    """A developer who wants to check the real thing only sets the variable."""
    result = _settings_module(
        "config.settings.dev",
        "print(s.CACHES['default']['BACKEND'])",
        REDIS_URL="redis://127.0.0.1:6379/1",
    )

    assert result.returncode == 0, result.stderr
    assert "redis" in result.stdout


def test_this_suite_runs_without_redis():
    assert "locmem" in settings.CACHES["default"]["BACKEND"]


def test_the_test_settings_believe_no_proxy():
    """Hermetic: a forwarded header is only trusted where a test says so."""
    assert settings.TRUSTED_PROXY_COUNT == 0
    assert settings.REST_FRAMEWORK["NUM_PROXIES"] == 0


# ---------------------------------------------------------------------------
# The proxy count production actually runs with
# ---------------------------------------------------------------------------
def test_production_trusts_exactly_one_proxy():
    result = _settings_module(
        "config.settings.prod",
        "print(s.TRUSTED_PROXY_COUNT); print(s.REST_FRAMEWORK['NUM_PROXIES'])",
        REDIS_URL="redis://127.0.0.1:6379/0",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["1", "1"]


def test_the_proxy_count_reaches_the_rate_limiter():
    """The freeze-at-import trap: the dictionary must carry the new number.

    `REST_FRAMEWORK` in base.py captures `TRUSTED_PROXY_COUNT` when it is
    built, so production setting the number alone would leave the rate limits
    reading the development default of zero — and the sign-in limit keyed on
    the proxy's address instead of the visitor's.
    """
    result = _settings_module(
        "config.settings.prod",
        "print(s.REST_FRAMEWORK['NUM_PROXIES'] == s.TRUSTED_PROXY_COUNT)",
        REDIS_URL="redis://127.0.0.1:6379/0",
        TRUSTED_PROXY_COUNT="2",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
