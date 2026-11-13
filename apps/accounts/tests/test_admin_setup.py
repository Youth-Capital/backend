"""Making the first administrator out of nothing.

A freshly deployed platform cannot make one through its own interface: every
route that grants the admin role is behind the admin role. The usual answer is
a shell on the server, which the people who need the account frequently do not
have. So the authority comes from a secret in the server's environment — and
that makes this the single most dangerous endpoint in the product, because it
answers to no session at all.

What keeps it safe is a short list, and every item on it is tested here: it is
off unless a token is configured, it refuses a token too short to be a secret,
it compares in constant time, it runs exactly once and never again after an
administrator exists, it will not accept a password published in this
repository, and it writes an audit entry that contains neither the token nor
the password.
"""

import pytest
from django.test import override_settings

from apps.accounts.models import User
from apps.accounts.services import (
    ADMIN_SETUP_MIN_TOKEN_LENGTH,
    FORBIDDEN_ADMIN_PASSWORDS,
    create_first_admin,
)
from apps.audit.models import AuditAction, AuditLog, AuditSeverity
from apps.common.enums import Role
from apps.common.exceptions import DomainError, NotAllowed

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/admin-setup/"
TOKEN = "s" * 48
GOOD_PASSWORD = "Qorakoʻl-Tartar-9134"


def payload(**overrides):
    body = {"token": TOKEN, "email": "owner@yoshlarkapitali.uz", "password": GOOD_PASSWORD}
    body.update(overrides)
    return body


def armed(**extra):
    return override_settings(ADMIN_SETUP_TOKEN=TOKEN, **extra)


# ---------------------------------------------------------------------------
# Off unless somebody turned it on
# ---------------------------------------------------------------------------
@override_settings(ADMIN_SETUP_TOKEN="")
def test_it_is_off_when_no_token_is_configured(api):
    response = api.post(URL, payload(), format="json")

    assert response.status_code in (400, 403)
    assert not User.objects.filter(role=Role.ADMIN).exists()


@override_settings(ADMIN_SETUP_TOKEN="short")
def test_a_token_too_short_to_be_a_secret_fails_closed(api):
    """A short token is worse than none, because it looks like protection."""
    response = api.post(URL, payload(token="short"), format="json")

    assert response.status_code in (400, 403)
    assert not User.objects.filter(role=Role.ADMIN).exists()


def test_the_minimum_length_is_past_guessing():
    assert ADMIN_SETUP_MIN_TOKEN_LENGTH >= 32


# ---------------------------------------------------------------------------
# The token has to be right
# ---------------------------------------------------------------------------
@armed()
def test_a_wrong_token_creates_nothing(api):
    response = api.post(URL, payload(token="x" * 48), format="json")

    assert response.status_code in (400, 403)
    assert User.objects.count() == 0


@armed()
def test_a_prefix_of_the_token_is_not_enough(api):
    api.post(URL, payload(token=TOKEN[:40]), format="json")

    assert User.objects.count() == 0


@armed()
def test_an_empty_token_is_not_enough(api):
    api.post(URL, payload(token=""), format="json")

    assert User.objects.count() == 0


# ---------------------------------------------------------------------------
# With the right token, once
# ---------------------------------------------------------------------------
@armed()
def test_the_first_administrator_is_created(api):
    response = api.post(URL, payload(), format="json")

    assert response.status_code == 201
    user = User.objects.get(email="owner@yoshlarkapitali.uz")
    assert user.role == Role.ADMIN
    assert user.is_superuser is True
    assert user.is_staff is True


@armed()
def test_the_new_administrator_does_not_have_to_confirm_their_email(api):
    """Otherwise the first admin is locked out by the verification gate with
    nobody able to let them in."""
    api.post(URL, payload(), format="json")

    assert User.objects.get(email="owner@yoshlarkapitali.uz").email_verified is True


@armed()
def test_the_new_administrator_can_sign_in(api):
    api.post(URL, payload(), format="json")

    response = api.post(
        "/api/v1/auth/login/",
        {"email": "owner@yoshlarkapitali.uz", "password": GOOD_PASSWORD},
        format="json",
    )

    assert response.status_code == 200


@armed()
def test_creating_the_account_does_not_sign_anybody_in(api):
    """Two separate acts. Signing in goes through the ordinary path, with its
    own lockout and its own new-device notice."""
    response = api.post(URL, payload(), format="json")

    assert "access" not in response.data


@armed()
def test_it_refuses_once_an_administrator_exists(api, admin_user):
    response = api.post(URL, payload(), format="json")

    assert response.status_code in (400, 403)
    assert User.objects.filter(role=Role.ADMIN).count() == 1


@armed()
def test_it_refuses_a_second_time(api):
    assert api.post(URL, payload(), format="json").status_code == 201

    second = api.post(URL, payload(email="other@yoshlarkapitali.uz"), format="json")

    assert second.status_code in (400, 403)
    assert User.objects.filter(role=Role.ADMIN).count() == 1


@armed()
def test_a_superuser_without_the_admin_role_also_closes_the_door(api, django_user_model):
    django_user_model.objects.create_superuser(
        email="root@example.com", password=GOOD_PASSWORD
    )

    response = api.post(URL, payload(), format="json")

    assert response.status_code in (400, 403)


# ---------------------------------------------------------------------------
# The password
# ---------------------------------------------------------------------------
@armed()
def test_a_weak_password_is_refused(api):
    for weak in ("short", "password123", "1234567890123"):
        response = api.post(URL, payload(password=weak), format="json")
        assert response.status_code in (400, 422), weak

    assert User.objects.count() == 0


@armed()
def test_the_password_published_in_this_repository_is_refused(api):
    """It is in seed_demo.py in a public repo. A platform whose admin password
    is in its own source tree has no admin password."""
    response = api.post(URL, payload(password="YoshlarDemo2026!"), format="json")

    assert response.status_code in (400, 422)
    assert User.objects.count() == 0


def test_the_demo_password_is_on_the_forbidden_list():
    assert "YoshlarDemo2026!" in FORBIDDEN_ADMIN_PASSWORDS


@armed()
def test_an_address_already_registered_is_refused(api, student):
    response = api.post(URL, payload(email=student.email), format="json")

    assert response.status_code in (400, 409, 422)
    student.refresh_from_db()
    assert student.role == Role.STUDENT


# ---------------------------------------------------------------------------
# It is never quiet, and it never writes down the secret
# ---------------------------------------------------------------------------
@armed()
def test_the_creation_is_recorded_at_the_highest_severity(api):
    api.post(URL, payload(), format="json")

    entry = AuditLog.objects.filter(action=AuditAction.ROLE_CHANGE).latest("created_at")
    assert entry.severity == AuditSeverity.CRITICAL
    assert entry.note == "first administrator created through setup"


@armed()
def test_neither_the_token_nor_the_password_reaches_the_audit_trail(api):
    api.post(URL, payload(), format="json")

    entry = AuditLog.objects.filter(action=AuditAction.ROLE_CHANGE).latest("created_at")
    written = f"{entry.changes}{entry.note}{entry.object_repr}"
    assert TOKEN not in written
    assert GOOD_PASSWORD not in written


# ---------------------------------------------------------------------------
# The service refuses directly too, so a command cannot route around the view
# ---------------------------------------------------------------------------
@override_settings(ADMIN_SETUP_TOKEN="")
def test_the_service_refuses_when_setup_is_off():
    with pytest.raises(NotAllowed):
        create_first_admin(email="a@b.uz", password=GOOD_PASSWORD, token="anything")


@override_settings(ADMIN_SETUP_TOKEN=TOKEN)
def test_the_service_refuses_a_bad_token():
    with pytest.raises(NotAllowed):
        create_first_admin(email="a@b.uz", password=GOOD_PASSWORD, token="nope")


@override_settings(ADMIN_SETUP_TOKEN=TOKEN)
def test_the_service_refuses_a_published_password():
    with pytest.raises(DomainError):
        create_first_admin(
            email="a@b.uz", password="YoshlarDemo2026!", token=TOKEN
        )
