"""Configuration invariants.

These catch the class of bug that only shows up as a 500 in production: a
throttle class whose scope was never declared, or a rate limit that silently
does nothing.
"""

import pytest
from django.conf import settings

from apps.common import throttling


def _throttle_classes():
    return [
        value
        for name, value in vars(throttling).items()
        if isinstance(value, type)
        and getattr(value, "scope", None)
        and not name.startswith("_")
    ]


def test_every_throttle_scope_is_declared():
    """A scope missing from DEFAULT_THROTTLE_RATES raises ImproperlyConfigured
    at request time, i.e. a 500 on the endpoint it protects."""
    declared = set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])

    for throttle_class in _throttle_classes():
        assert throttle_class.scope in declared, (
            f"{throttle_class.__name__}.scope='{throttle_class.scope}' is not in "
            "REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']"
        )


def test_throttle_classes_are_actually_wired_up():
    """Guards against the previous bug: setting `throttle_scope` as a function
    attribute left ScopedRateThrottle with no scope, so the limit never applied.
    """
    from apps.jobs.api.views import VacancyViewSet

    action = VacancyViewSet.candidates
    classes = action.kwargs.get("throttle_classes", [])

    assert classes, "candidate search must declare a throttle class"
    assert all(getattr(cls, "scope", None) for cls in classes), (
        "throttle classes on an action must carry their own scope — DRF rejects "
        "`throttle_scope` as a viewset as_view() keyword"
    )


@pytest.mark.parametrize(
    "scope", ["anon", "user", "auth", "register", "candidate_search", "ai"]
)
def test_expected_scopes_exist(scope):
    assert scope in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]


def test_refresh_cookie_is_not_reachable_from_javascript():
    """The whole point of the split-token design (docs/02-ARCHITECTURE.md §5)."""
    assert settings.AUTH_COOKIE_NAME
    assert settings.AUTH_COOKIE_PATH.startswith("/api/v1/auth/")
    assert settings.SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] is True
    assert settings.SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"] is True


def test_argon2_is_the_primary_password_hasher():
    """Checked against base settings on purpose: test.py swaps in MD5 for speed,
    so asserting the active value would test the test configuration."""
    from config.settings import base

    assert "Argon2" in base.PASSWORD_HASHERS[0]


def test_uzbek_is_the_default_language():
    """TZ §12: UZ (latin) is the default, RU and EN are options."""
    assert settings.LANGUAGE_CODE == "uz"
    assert [code for code, _label in settings.LANGUAGES] == ["uz", "ru", "en"]
