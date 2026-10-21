"""The full record of one person, for an administrator — and what is not in it.

An administrator handling a support request should be able to see who somebody
is without opening six screens. Two things are worth pinning about that record:
only an administrator may read it, and the password is not in it.

The password cannot be in it. What the account stores is an Argon2id hash — a
one-way function with a per-account salt — so there is no plaintext to return
from anywhere. The hash is kept out too: handed to anyone it becomes material
for guessing the one secret the account holder controls.
"""

import pytest

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/admin/users/{id}/overview/"


@pytest.fixture
def subject(student, taxonomy, give_skill):
    """Someone with a record worth reading."""
    give_skill(student, taxonomy["python"], 70)
    return student


# ---------------------------------------------------------------------------
# Who may read it
# ---------------------------------------------------------------------------
def test_an_admin_sees_the_whole_record(auth, admin_user, subject):
    response = auth(admin_user).get(URL.format(id=subject.id))

    assert response.status_code == 200, response.data
    body = response.data
    assert body["account"]["email"] == subject.email
    assert body["profile"]["youth_id"]
    assert body["skills"]["items"]


def test_a_student_cannot_read_anybody(auth, student, other_student):
    assert auth(student).get(URL.format(id=other_student.id)).status_code == 403


def test_a_student_cannot_read_even_their_own(auth, student):
    """This is the administrator's view, not a self-service one."""
    assert auth(student).get(URL.format(id=student.id)).status_code == 403


def test_an_employer_cannot_read_a_candidate(auth, employer, subject):
    assert auth(employer).get(URL.format(id=subject.id)).status_code == 403


def test_signed_out_is_refused(api, subject):
    assert api.get(URL.format(id=subject.id)).status_code in {401, 403}


# ---------------------------------------------------------------------------
# The password
# ---------------------------------------------------------------------------
def test_the_password_hash_never_leaves_the_database(auth, admin_user, subject):
    """The strongest form of this test: the stored hash appears nowhere."""
    subject.refresh_from_db()
    stored = subject.password
    # Shape, not algorithm: this suite runs a fast hasher on purpose, while
    # every real environment runs Argon2id (config/settings/base.py). What is
    # being tested holds either way — whatever is in that column stays there.
    assert "$" in stored and len(stored) > 20, "that is not a password hash"

    response = auth(admin_user).get(URL.format(id=subject.id))

    body = str(response.content, "utf-8")
    assert stored not in body
    # Nor any fragment of it long enough to be useful.
    assert stored.split("$")[-1][:16] not in body


def test_the_record_says_why_there_is_no_password(auth, admin_user, subject):
    """An administrator looking for it should find the reason, not a blank."""
    response = auth(admin_user).get(URL.format(id=subject.id))

    assert "not stored" in response.data["account"]["password"]


def test_no_field_anywhere_carries_a_password(auth, admin_user, subject):
    response = auth(admin_user).get(URL.format(id=subject.id))

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                # The one mention is the explanation on the account block.
                if "password" in key.lower():
                    assert "not stored" in str(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(response.data)


# ---------------------------------------------------------------------------
# Reading somebody's file is itself an event
# ---------------------------------------------------------------------------
def test_opening_the_record_is_audited(auth, admin_user, subject):
    from apps.audit.models import AuditAction, AuditLog

    auth(admin_user).get(URL.format(id=subject.id))

    entry = AuditLog.objects.filter(action=AuditAction.PII_ACCESS).first()
    assert entry is not None, "reading a personal record left no audit trail"
    assert entry.actor_id == admin_user.id
    assert str(subject.id) in (entry.object_id or "")


def test_a_refused_read_records_nothing(auth, student, other_student):
    from apps.audit.models import AuditAction, AuditLog

    auth(student).get(URL.format(id=other_student.id))

    assert not AuditLog.objects.filter(action=AuditAction.PII_ACCESS).exists()


# ---------------------------------------------------------------------------
# What it carries
# ---------------------------------------------------------------------------
def test_the_record_covers_every_part_of_the_platform(auth, admin_user, subject):
    body = auth(admin_user).get(URL.format(id=subject.id)).data

    for section in (
        "account",
        "profile",
        "skills",
        "learning",
        "assessment",
        "experience",
        "billing",
        "consents",
        "devices",
        "recent_activity",
    ):
        assert section in body, f"{section} is missing from the record"


def test_an_employer_account_shows_its_company_and_no_student_profile(
    auth, admin_user, employer
):
    body = auth(admin_user).get(URL.format(id=employer.id)).data

    assert body["profile"] is None
    assert body["company"]["display_name"]
