"""Refreshing a session is not the same act as signing in.

Both used to share one 5/min budget. Every full page load spends a refresh, so
six reloads in a minute reached the sign-in limit, and the client — which read
any failed refresh as a dead session — dropped the person back to the login
page. Replay of a refresh token is already handled by rotation plus
blacklisting, so this rate is a flood stop rather than the defence.

The test settings switch throttling off on purpose, so asserting on live
requests here would prove nothing. What regressed was the *configuration*, and
that is what these read: the rates the product actually ships with, and the
scope the view asks for.
"""

import contextlib
import importlib
import os
import pkgutil
from unittest import mock

import pytest
from rest_framework.views import APIView

import apps
from apps.accounts.api.views import ChangePasswordView, LoginView, RefreshView

BASE = importlib.import_module("config.settings.base")
RATES = BASE.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

#: Every settings module the product can boot with.
ENVIRONMENTS = ["base", "dev", "prod", "test"]


def declared_scopes() -> set[str]:
    """Every throttle scope any view in the project asks for."""
    scopes = set()
    for module in pkgutil.walk_packages(apps.__path__, prefix="apps."):
        if not module.name.endswith(".api.views"):
            continue
        try:
            imported = importlib.import_module(module.name)
        except Exception:  # noqa: BLE001 - a broken import is another test's job
            continue
        for value in vars(imported).values():
            if isinstance(value, type) and issubclass(value, APIView):
                scope = getattr(value, "throttle_scope", None)
                if scope:
                    scopes.add(scope)
    return scopes


#: prod.py refuses to import without real secrets, and rightly so. Placeholders
#: let the throttle configuration be read without loosening that rule.
PRODUCTION_PLACEHOLDERS = {
    "DJANGO_SECRET_KEY": "test-only-not-a-real-key",
    "EMAIL_HOST": "smtp.example.invalid",
    "EMAIL_HOST_USER": "noreply@example.invalid",
    "EMAIL_HOST_PASSWORD": "placeholder",
    "DEFAULT_FROM_EMAIL": "noreply@example.invalid",
    "SENTRY_DSN": "",
    "SENTRY_ENVIRONMENT": "test",
}


@contextlib.contextmanager
def production_env():
    with mock.patch.dict(os.environ, PRODUCTION_PLACEHOLDERS, clear=False):
        yield


def per_minute(rate: str) -> float:
    """DRF rates read "<count>/<period>"; normalise them to a per-minute number."""
    count, period = rate.split("/")
    seconds = {"s": 1, "sec": 1, "min": 60, "hour": 3600, "day": 86400}[period]
    return float(count) * 60 / seconds


def test_refreshing_has_its_own_budget():
    assert "refresh" in RATES, "the refresh scope is gone"
    assert RATES["refresh"] != RATES["auth"], (
        "refresh shares the sign-in budget again — a handful of page loads "
        "will sign people out"
    )


def test_refreshing_allows_more_than_a_few_page_loads_a_minute():
    """One refresh per full page load, so this is the reload budget."""
    assert per_minute(RATES["refresh"]) >= 30, (
        f"{RATES['refresh']} is too tight: a person reloading a few tabs "
        "would be signed out"
    )


def test_signing_in_stays_tight():
    """The limit on guessing has to stay a limit on guessing."""
    assert per_minute(RATES["auth"]) <= 10, (
        f"{RATES['auth']} is too loose for a sign-in endpoint"
    )


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_every_scope_a_view_asks_for_exists_in_every_environment(environment):
    """A scope with no rate is not a missing limit — it is a 500.

    DRF raises when a ScopedRateThrottle cannot find its key, so a settings
    file that lists the rates again instead of merging them takes the view
    down completely. Adding the refresh scope did exactly that in dev: sign-in
    worked and every page load afterwards returned 500.
    """
    with production_env():
        settings_module = importlib.import_module(f"config.settings.{environment}")
    rates = settings_module.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

    missing = sorted(declared_scopes() - set(rates))
    assert not missing, (
        f"config.settings.{environment} has no rate for {missing} — "
        "every request to those views will fail"
    )


@pytest.mark.parametrize(
    "view,scope",
    [
        (LoginView, "auth"),
        (ChangePasswordView, "auth"),
        (RefreshView, "refresh"),
    ],
)
def test_each_view_asks_for_the_scope_it_should(view, scope):
    """A rate nobody points at protects nothing."""
    assert view.throttle_scope == scope
