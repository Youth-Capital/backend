"""The two endpoints a cookie alone can drive must not be drivable cross-site.

`/auth/refresh/` and `/auth/logout/` are the only endpoints authenticated by the
refresh cookie rather than a header, which makes them the only ones a hostile
page can reach with the victim's credentials attached. With `SameSite=Lax` —
the default — a cross-site POST carries no cookie and there is nothing to
steal. But the setting comes from the environment, and a deployment that splits
the API and the SPA across two sites must switch it to `None`. From then on any
site could make the victim's browser rotate their session (invalidating the
tab they have open) or log them out.

The defence is a header the SPA sends and a cross-site form cannot. A page on
another origin that tries to add it has to ask the browser's permission first,
and CORS tells the browser no. Where the cookie is `SameSite=None` the request's
Origin is checked against the configured list as well.
"""

import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"

SPA = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def signed_in(api, student):
    """A client holding a refresh cookie, the way a browser would."""
    response = api.post(
        LOGIN, {"email": student.email, "password": "TestPass12345"}, format="json"
    )
    assert response.status_code == 200, response.data
    return api


# ---------------------------------------------------------------------------
# Without the header: a form on another site
# ---------------------------------------------------------------------------
def test_a_bare_post_cannot_rotate_the_session(signed_in):
    response = signed_in.post(REFRESH)

    assert response.status_code == 403
    # And the session it was aimed at is untouched: the real SPA still refreshes.
    assert signed_in.post(REFRESH, **SPA).status_code == 200


def test_a_bare_post_cannot_log_somebody_out(signed_in):
    response = signed_in.post(LOGOUT)

    assert response.status_code == 403
    assert signed_in.post(REFRESH, **SPA).status_code == 200


# ---------------------------------------------------------------------------
# With the header: the SPA
# ---------------------------------------------------------------------------
def test_the_spa_refreshes(signed_in):
    response = signed_in.post(REFRESH, **SPA)

    assert response.status_code == 200
    assert response.data["access"]


def test_the_spa_logs_out(signed_in):
    assert signed_in.post(LOGOUT, **SPA).status_code == 204
    # Logged out means the old cookie is dead.
    assert signed_in.post(REFRESH, **SPA).status_code in {400, 401}


# ---------------------------------------------------------------------------
# A cross-site deployment
# ---------------------------------------------------------------------------
@override_settings(
    AUTH_COOKIE_SAMESITE="None",
    CORS_ALLOWED_ORIGINS=["https://app.example.test"],
    CSRF_TRUSTED_ORIGINS=["https://app.example.test"],
)
def test_with_samesite_none_a_foreign_origin_is_refused(signed_in):
    response = signed_in.post(REFRESH, HTTP_ORIGIN="https://evil.example", **SPA)

    assert response.status_code == 403


@override_settings(
    AUTH_COOKIE_SAMESITE="None",
    CORS_ALLOWED_ORIGINS=["https://app.example.test"],
    CSRF_TRUSTED_ORIGINS=["https://app.example.test"],
)
def test_with_samesite_none_the_configured_origin_still_works(signed_in):
    response = signed_in.post(REFRESH, HTTP_ORIGIN="https://app.example.test", **SPA)

    assert response.status_code == 200


@override_settings(
    AUTH_COOKIE_SAMESITE="None",
    CORS_ALLOWED_ORIGINS=["https://app.example.test"],
    CSRF_TRUSTED_ORIGINS=["https://app.example.test"],
)
def test_with_samesite_none_logout_is_guarded_the_same_way(signed_in):
    response = signed_in.post(LOGOUT, HTTP_ORIGIN="https://evil.example", **SPA)

    assert response.status_code == 403
    assert signed_in.post(
        REFRESH, HTTP_ORIGIN="https://app.example.test", **SPA
    ).status_code == 200
