"""Closing your own account.

The thing being pinned here is not that a row disappears — it deliberately does
not. It is that nothing on the account still points at the person: the name is
gone, the address cannot be routed to, the password cannot be used, every
session is dead, and the documents the account holder wrote have been removed
rather than merely flagged. The row that remains exists so the records other
people depend on — an employer's applications, the audit trail — do not fall
over with it.

The password is asked for again on the way in, because a signed-in tab is not
authority for something irreversible.
"""

import pytest
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from apps.accounts.models import User
from apps.accounts.services import delete_own_account
from apps.audit.models import AuditAction, AuditLog
from apps.common.enums import Role
from apps.common.exceptions import DomainError, NotAllowed
from apps.cv.models import CVDocument, PortfolioItem
from apps.experience.models import Experience, ExperienceType
from apps.profiles.models import Education

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/me/delete/"
PASSWORD = "TestPass12345"


@pytest.fixture
def furnished(student):
    """A student with documents, so there is something to erase."""
    CVDocument.objects.create(user=student, title="My CV", headline="Analyst")
    PortfolioItem.objects.create(user=student, title="A project", description="Built it")
    Education.objects.create(user=student, institution="INHA", degree="BSc")
    Experience.objects.create(
        user=student,
        type=ExperienceType.WORK,
        title="Intern",
        organization="SafeNet",
    )
    return student


# ---------------------------------------------------------------------------
# It closes the account
# ---------------------------------------------------------------------------
def test_the_account_is_closed(auth, student):
    response = auth(student).post(URL, {"current_password": PASSWORD}, format="json")

    assert response.status_code == 204
    student.refresh_from_db()
    assert student.is_active is False


def test_the_address_is_replaced_with_one_that_cannot_be_reached(auth, student):
    auth(student).post(URL, {"current_password": PASSWORD}, format="json")

    student.refresh_from_db()
    assert student.email == f"deleted-{student.id}@deleted.invalid"
    # RFC 2606 reserves .invalid precisely so that it can never resolve.
    assert student.email.endswith(".invalid")


def test_the_password_can_no_longer_be_used(auth, student):
    auth(student).post(URL, {"current_password": PASSWORD}, format="json")

    student.refresh_from_db()
    assert student.has_usable_password() is False
    assert student.check_password(PASSWORD) is False


def test_the_old_address_is_free_for_somebody_else(auth, student):
    """Replacing rather than keeping the address means the person can come
    back under the same email, which a tombstone row would otherwise block."""
    old = student.email

    auth(student).post(URL, {"current_password": PASSWORD}, format="json")

    assert not User.objects.filter(email=old).exists()


# ---------------------------------------------------------------------------
# Nothing on it still names the person
# ---------------------------------------------------------------------------
def test_the_profile_is_emptied(auth, furnished):
    auth(furnished).post(URL, {"current_password": PASSWORD}, format="json")

    profile = furnished.student_profile
    profile.refresh_from_db()
    assert profile.first_name == ""
    assert profile.last_name == ""
    assert profile.city == ""


def test_the_documents_the_account_wrote_are_removed(auth, furnished):
    auth(furnished).post(URL, {"current_password": PASSWORD}, format="json")

    assert CVDocument.objects.filter(user=furnished).count() == 0
    assert PortfolioItem.objects.filter(user=furnished).count() == 0
    assert Education.objects.filter(user=furnished).count() == 0
    assert Experience.objects.filter(user=furnished).count() == 0


def test_removal_is_real_and_not_a_flag(auth, furnished):
    """A flagged row still holds the title and the text somebody asked to have
    removed, so these models must not be the soft-deleting kind."""
    assert not hasattr(CVDocument, "deleted_at"), "a soft delete would keep the text"
    assert not hasattr(PortfolioItem, "deleted_at")

    auth(furnished).post(URL, {"current_password": PASSWORD}, format="json")

    assert not CVDocument.objects.filter(user=furnished).exists()


def test_every_session_is_revoked(auth, student):
    client = auth(student)
    outstanding = OutstandingToken.objects.filter(user=student).count()

    client.post(URL, {"current_password": PASSWORD}, format="json")

    assert outstanding > 0, "the fixture should have issued a refresh token"
    assert BlacklistedToken.objects.filter(token__user=student).count() == outstanding


# ---------------------------------------------------------------------------
# It asks for the password, and refuses without it
# ---------------------------------------------------------------------------
def test_the_wrong_password_closes_nothing(auth, student):
    response = auth(student).post(URL, {"current_password": "not-it"}, format="json")

    assert response.status_code in (400, 422)
    student.refresh_from_db()
    assert student.is_active is True
    assert student.check_password(PASSWORD) is True


def test_the_password_is_required(auth, student):
    response = auth(student).post(URL, {}, format="json")

    assert response.status_code == 400
    student.refresh_from_db()
    assert student.is_active is True


def test_an_anonymous_caller_is_refused(api, student):
    assert api.post(URL, {"current_password": PASSWORD}, format="json").status_code == 401
    student.refresh_from_db()
    assert student.is_active is True


# ---------------------------------------------------------------------------
# An administrator cannot close their own account from here
# ---------------------------------------------------------------------------
def test_an_admin_cannot_close_their_own_account(auth, admin_user):
    response = auth(admin_user).post(URL, {"current_password": PASSWORD}, format="json")

    assert response.status_code in (400, 403)
    admin_user.refresh_from_db()
    assert admin_user.is_active is True


def test_the_service_refuses_an_admin_directly(admin_user):
    with pytest.raises(NotAllowed):
        delete_own_account(admin_user, PASSWORD)


def test_the_service_refuses_a_wrong_password_directly(student):
    with pytest.raises(DomainError):
        delete_own_account(student, "not-it")


# ---------------------------------------------------------------------------
# It leaves a trail, because somebody will ask what happened
# ---------------------------------------------------------------------------
def test_the_closure_is_recorded(auth, student):
    auth(student).post(URL, {"current_password": PASSWORD}, format="json")

    entry = AuditLog.objects.filter(action=AuditAction.DELETE).order_by("-created_at").first()
    assert entry is not None
    assert entry.note == "account closed by its owner"


def test_the_trail_identifies_the_account_without_naming_the_person(auth, student):
    """An audit log is the last place anyone thinks to look for data that was
    asked to be removed, so the entry carries the id and not the address."""
    old = student.email

    auth(student).post(URL, {"current_password": PASSWORD}, format="json")

    entry = AuditLog.objects.filter(action=AuditAction.DELETE).order_by("-created_at").first()
    assert entry.object_id == str(student.id)
    assert old not in str(entry.changes)
    assert old not in entry.actor_email


# ---------------------------------------------------------------------------
# A closed account cannot be signed back into
# ---------------------------------------------------------------------------
def test_the_closed_account_cannot_sign_in(auth, api, student):
    email = student.email
    auth(student).post(URL, {"current_password": PASSWORD}, format="json")

    api.credentials()
    response = api.post(
        "/api/v1/auth/login/", {"email": email, "password": PASSWORD}, format="json"
    )

    assert response.status_code != 200


def test_a_student_keeps_their_role_for_the_records_that_reference_it(auth, student):
    auth(student).post(URL, {"current_password": PASSWORD}, format="json")

    student.refresh_from_db()
    assert student.role == Role.STUDENT
