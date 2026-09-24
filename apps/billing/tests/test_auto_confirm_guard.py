"""Free paid plans must be impossible to switch on by accident in production.

`BILLING_MANUAL_AUTO_CONFIRM` makes the manual provider settle every payment
without money moving. That is right for a demo and for tests, and it defaults
to DEBUG, so production starts with it off. But it is read from the
environment, and one stray line in a server's `.env` would have turned every
checkout into a free upgrade — silently, because nothing looked at it again.

Two guards now look: the production settings module refuses to load with it
on, and a system check refuses any environment that is not in DEBUG.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import override_settings

from apps.billing.checks import check_manual_auto_confirm

BACKEND_ROOT = Path(__file__).resolve().parents[3]

#: The least a production settings import needs, with obviously fake values.
PRODUCTION_ENV = {
    "DJANGO_SECRET_KEY": "test-only-fake-signing-key-9f3Kq2Lm8Vx4Rt7Zp1Nb6Hc5Wd0Ys3Ju",
    "DJANGO_ALLOWED_HOSTS": "example.test",
    "EMAIL_HOST": "smtp.example.test",
    "EMAIL_HOST_USER": "mailer",
    "EMAIL_HOST_PASSWORD": "not-a-real-password",
}


def _load_production_settings(**overrides):
    env = {**os.environ, **PRODUCTION_ENV, **overrides}
    env.pop("DJANGO_SETTINGS_MODULE", None)
    return subprocess.run(
        [sys.executable, "-c", "import config.settings.prod"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_production_refuses_to_start_with_auto_confirm_on():
    result = _load_production_settings(BILLING_MANUAL_AUTO_CONFIRM="true")

    assert result.returncode != 0
    assert "BILLING_MANUAL_AUTO_CONFIRM" in result.stderr


def test_production_starts_normally_with_it_off():
    result = _load_production_settings(BILLING_MANUAL_AUTO_CONFIRM="false")

    assert result.returncode == 0, result.stderr


@override_settings(DEBUG=False, BILLING_MANUAL_AUTO_CONFIRM=True)
def test_the_system_check_rejects_it_outside_debug():
    errors = check_manual_auto_confirm(None)

    assert [error.id for error in errors] == ["billing.E001"]


@pytest.mark.parametrize(
    "debug, enabled",
    [(True, True), (False, False), (True, False)],
)
def test_the_system_check_allows_the_safe_combinations(debug, enabled):
    with override_settings(DEBUG=debug, BILLING_MANUAL_AUTO_CONFIRM=enabled):
        assert check_manual_auto_confirm(None) == []
