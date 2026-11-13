"""Nobody makes themselves an administrator.

The admin surface is the whole audit trail, every user record and the full
personal data behind each one. So the question that matters is not whether the
code currently refuses self-promotion — it does, in three separate places —
but whether it will still refuse it after somebody edits one of them.

The three places, and what each of them is:

  * registration refuses the role outright, so it cannot be claimed on the
    way in;
  * `role` is read-only on the account serializer, so it cannot be written by
    the person it belongs to;
  * the only endpoint that writes it sits behind IsAdmin and refuses the
    acting admin's own account, so one admin cannot quietly widen their own
    authority and no admin can be their own promoter.

Each of those was a decision, and until this file existed none of them had a
test. That is the gap this closes: the same argument `test_access_contract`
makes about querysets, made about the one field that decides who sees
everything.
"""

import pytest

from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog, AuditSeverity
from apps.common.enums import Role

pytestmark = pytest.mark.django_db

REGISTER = "/api/v1/auth/register/"
ME = "/api/v1/auth/me/"


def registration(**overrides):
    payload = {
        "email": "claimant@example.com",
        "password": "TestPass12345",
        "role": "STUDENT",
        "preferred_language": "ru",
        "birth_date": "2000-01-01",
        "first_name": "Claimant",
        "consents": ["TERMS", "PRIVACY", "DATA_PROCESSING"],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# It cannot be claimed on the way in
# ---------------------------------------------------------------------------
def test_nobody_can_register_as_an_administrator(api):
    response = api.post(REGISTER, registration(role="ADMIN"), format="json")

    assert response.status_code in (400, 403, 422)
    assert not User.objects.filter(email="claimant@example.com").exists()


def test_the_roles_that_can_be_registered_are_the_two_harmless_ones():
    from apps.accounts.services import SELF_SERVICE_ROLES

    assert SELF_SERVICE_ROLES == {Role.STUDENT, Role.EMPLOYER}


# ---------------------------------------------------------------------------
# It cannot be written by the person it belongs to
# ---------------------------------------------------------------------------
def test_a_student_cannot_promote_themselves_through_their_own_profile(auth, student):
    response = auth(student).patch(ME, {"role": "ADMIN"}, format="json")

    student.refresh_from_db()
    assert student.role == Role.STUDENT, (
        "role must stay read-only on the account serializer"
    )
    # The request itself may succeed — DRF drops read-only fields silently —
    # so the assertion that matters is the one above.
    assert response.status_code in (200, 400)


def test_an_employer_cannot_promote_themselves_either(auth, employer):
    auth(employer).patch(ME, {"role": "ADMIN"}, format="json")

    employer.refresh_from_db()
    assert employer.role == Role.EMPLOYER


def test_the_account_serializer_keeps_the_role_read_only():
    from apps.accounts.api.serializers import UserSerializer

    assert "role" in UserSerializer.Meta.read_only_fields


# ---------------------------------------------------------------------------
# The one endpoint that writes it is for administrators only
# ---------------------------------------------------------------------------
def change_role_url(user) -> str:
    return f"/api/v1/auth/admin/users/{user.id}/change-role/"


def test_a_student_cannot_reach_the_role_endpoint(auth, student, other_student):
    response = auth(student).post(
        change_role_url(other_student), {"role": "ADMIN"}, format="json"
    )

    assert response.status_code in (403, 404)
    other_student.refresh_from_db()
    assert other_student.role == Role.STUDENT


def test_a_student_cannot_aim_it_at_themselves(auth, student):
    response = auth(student).post(change_role_url(student), {"role": "ADMIN"}, format="json")

    assert response.status_code in (403, 404)
    student.refresh_from_db()
    assert student.role == Role.STUDENT


def test_an_employer_cannot_reach_it(auth, employer, student):
    response = auth(employer).post(change_role_url(student), {"role": "ADMIN"}, format="json")

    assert response.status_code in (403, 404)
    student.refresh_from_db()
    assert student.role == Role.STUDENT


def test_an_anonymous_caller_cannot_reach_it(api, student):
    response = api.post(change_role_url(student), {"role": "ADMIN"}, format="json")

    assert response.status_code in (401, 403)
    student.refresh_from_db()
    assert student.role == Role.STUDENT


# ---------------------------------------------------------------------------
# An administrator cannot be their own promoter
# ---------------------------------------------------------------------------
def test_an_admin_cannot_change_their_own_role(auth, admin_user):
    """Not about promotion — an admin is already one. It stops an admin from
    quietly rewriting their own authority without a second person seeing it."""
    response = auth(admin_user).post(
        change_role_url(admin_user), {"role": "STUDENT"}, format="json"
    )

    assert response.status_code in (400, 403)
    admin_user.refresh_from_db()
    assert admin_user.role == Role.ADMIN


def test_an_admin_may_promote_somebody_else(auth, admin_user, student):
    """The positive case, so the tests above are proved to be testing a guard
    rather than an endpoint that never works."""
    response = auth(admin_user).post(
        change_role_url(student), {"role": "ADMIN"}, format="json"
    )

    assert response.status_code == 200
    student.refresh_from_db()
    assert student.role == Role.ADMIN
    assert student.is_staff is True


def test_an_unknown_role_is_refused(auth, admin_user, student):
    response = auth(admin_user).post(
        change_role_url(student), {"role": "SUPERADMIN"}, format="json"
    )

    assert response.status_code in (400, 422)
    student.refresh_from_db()
    assert student.role == Role.STUDENT


# ---------------------------------------------------------------------------
# And it is never quiet
# ---------------------------------------------------------------------------
def test_a_promotion_is_recorded_at_the_highest_severity(auth, admin_user, student):
    auth(admin_user).post(change_role_url(student), {"role": "ADMIN"}, format="json")

    entry = AuditLog.objects.filter(action=AuditAction.ROLE_CHANGE).latest("created_at")
    assert entry.severity == AuditSeverity.CRITICAL
    assert entry.changes["before"]["role"] == Role.STUDENT
    assert entry.changes["after"]["role"] == Role.ADMIN
    assert entry.object_id == str(student.id)
