"""Who is allowed to create MENTOR-weight skill evidence.

Mentor assessments carry 0.85 confidence — just below a graded test and well
above a self-declared skill. That weight is only defensible if the person
issuing it is a real, checked mentor, so these are the tests that keep the
evidence model honest.
"""

import pytest
from django.utils import timezone

from apps.common.enums import EvidenceSource, Role, VerificationStatus
from apps.mentorship.models import (
    MentorFeedback,
    MentorSession,
    MentorSkillAssessment,
    SessionStatus,
)
from apps.profiles.models import MentorProfile, SkillEvidence
from apps.taxonomy.models import Skill, SkillCategory

pytestmark = pytest.mark.django_db


@pytest.fixture
def skill():
    category = SkillCategory.objects.create(name_uz="Ma'lumot", slug="data")
    return Skill.objects.create(name_uz="SQL", slug="sql", category=category)


@pytest.fixture
def student(django_user_model):
    return django_user_model.objects.create_user(
        email="learner@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )


def _mentor(django_user_model, email: str, status: str) -> MentorProfile:
    user = django_user_model.objects.create_user(
        email=email, password="Str0ng!passw0rd", role=Role.MENTOR
    )
    return MentorProfile.objects.create(
        user=user,
        first_name="Mentor",
        last_name=status.title(),
        verification_status=status,
    )


def _completed_session(mentor, student):
    return MentorSession.objects.create(
        mentor=mentor,
        student=student,
        topic="SQL review",
        scheduled_at=timezone.now(),
        status=SessionStatus.COMPLETED,
    )


def _assess(session, author, skill, score=80):
    feedback = MentorFeedback.objects.create(
        session=session, author=author, rating=5, comment=""
    )
    return MentorSkillAssessment.objects.create(
        feedback=feedback, skill=skill, score=score
    )


def test_verified_mentor_assessment_becomes_evidence(
    django_user_model, student, skill
):
    mentor = _mentor(django_user_model, "ok@example.com", VerificationStatus.VERIFIED)
    session = _completed_session(mentor, student)

    _assess(session, mentor.user, skill)

    evidence = SkillEvidence.objects.filter(
        user_skill__user=student, source=EvidenceSource.MENTOR
    )
    assert evidence.count() == 1


def test_unverified_mentor_assessment_is_not_evidence(
    django_user_model, student, skill
):
    """The exploit this guards: register as a mentor, assess your own alt account.

    Mentor sign-up is self-service and starts PENDING, so without the
    verification check one person controlling two accounts could mint
    near-test-grade proof for any skill.
    """
    mentor = _mentor(django_user_model, "pending@example.com", VerificationStatus.PENDING)
    session = _completed_session(mentor, student)

    _assess(session, mentor.user, skill)

    assert not SkillEvidence.objects.filter(
        user_skill__user=student, source=EvidenceSource.MENTOR
    ).exists()


def test_rejected_mentor_assessment_is_not_evidence(
    django_user_model, student, skill
):
    mentor = _mentor(django_user_model, "no@example.com", VerificationStatus.REJECTED)
    session = _completed_session(mentor, student)

    _assess(session, mentor.user, skill)

    assert not SkillEvidence.objects.filter(
        user_skill__user=student, source=EvidenceSource.MENTOR
    ).exists()


def test_student_cannot_assess_their_own_skills(django_user_model, student, skill):
    """The feedback form is open to both parties; the evidence path is not."""
    mentor = _mentor(django_user_model, "v@example.com", VerificationStatus.VERIFIED)
    session = _completed_session(mentor, student)

    _assess(session, student, skill, score=100)

    assert not SkillEvidence.objects.filter(
        user_skill__user=student, source=EvidenceSource.MENTOR
    ).exists()


def test_booking_an_unverified_mentor_is_refused(
    django_user_model, student, skill
):
    from rest_framework.test import APIClient

    mentor = _mentor(django_user_model, "hidden@example.com", VerificationStatus.PENDING)

    client = APIClient()
    client.force_authenticate(user=student)
    response = client.post(
        "/api/v1/mentorship/sessions/",
        {
            "mentor": str(mentor.id),
            "topic": "SQL",
            "scheduled_at": timezone.now().isoformat(),
        },
        format="json",
    )

    # The directory hides unverified mentors, but the endpoint accepts any id,
    # so the refusal has to come from the server.
    assert response.status_code in (400, 403)
