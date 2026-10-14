"""Probes stay available under load and report dependency outages."""

from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_readiness_checks_database_and_cache():
    response = APIClient().get("/api/v1/health/ready/")
    assert response.status_code == 200
    assert response.data == {
        "status": "ok", "checks": {"database": "ok", "cache": "ok"},
    }


@pytest.mark.django_db
def test_readiness_fails_when_cache_is_unavailable():
    with patch("apps.common.api.views.cache.get", side_effect=ConnectionError):
        response = APIClient().get("/api/v1/health/ready/")
    assert response.status_code == 503
    assert response.data["checks"]["cache"] == "error: ConnectionError"


@pytest.mark.django_db
def test_readiness_fails_when_database_is_unavailable():
    with patch("apps.common.api.views.connection.cursor", side_effect=ConnectionError):
        response = APIClient().get("/api/v1/health/ready/")
    assert response.status_code == 503
    assert response.data["checks"]["database"] == "error: ConnectionError"


def test_liveness_does_not_touch_dependencies_or_throttles():
    with patch("apps.common.api.views.connection.cursor", side_effect=AssertionError), \
         patch("apps.common.api.views.cache.get", side_effect=AssertionError):
        response = APIClient().get("/api/v1/health/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_probes_are_not_rate_limited():
    throttles = {"DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
    ], "DEFAULT_THROTTLE_RATES": {"anon": "1/day"}}
    with override_settings(REST_FRAMEWORK=throttles):
        client = APIClient()
        for _ in range(3):
            assert client.get("/api/v1/health/").status_code == 200
            assert client.get("/api/v1/health/ready/").status_code == 200
