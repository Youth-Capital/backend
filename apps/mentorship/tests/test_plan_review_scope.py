"""Whose plans a mentor may review.

Reviewing a development plan is not a comment: approving one stamps
`approved_by` on it, and the platform treats an approved plan as one a
qualified person stood behind. So the question of *which* plans a given mentor
may act on is an authorisation question, not a tidiness one.

The endpoint takes a plan id from the request body. Reads were already scoped
to the reviewer's own reviews; creation was not scoped to anything.
"""

import pytest

pytestmark = pytest.mark.django_db

REVIEWS_URL = "/api/v1/mentorship/plan-reviews/"


@pytest.fixture
def mentor(db):
    from apps.accounts.models import User
    from apps.common.enums import Role
    from apps.profiles.models import MentorProfile

    user = User.objects.create_user(
        email="mentor@test.uz", password="TestPass12345", role=Role.MENTOR
    )
    MentorProfile.objects.create(user=user, first_name="M", last_name="One")
    return user


@pytest.fixture
def other_mentor(db):
    from apps.accounts.models import User
    from apps.common.enums import Role
    from apps.profiles.models import MentorProfile

    user = User.objects.create_user(
        email="stranger@test.uz", password="TestPass12345", role=Role.MENTOR
    )
    MentorProfile.objects.create(user=user, first_name="M", last_name="Two")
    return user


@pytest.fixture
def plan(db, student):
    from apps.idp.models import DevelopmentPlan

    return DevelopmentPlan.objects.create(user=student, title="Plan", period_days=30)


@pytest.fixture
def session(db, mentor, student):
    """The relationship that makes somebody this mentor's student."""
    from apps.mentorship.models import MentorSession

    return MentorSession.objects.create(
        mentor=mentor.mentor_profile, student=student, topic="Career"
    )


def test_a_mentor_can_review_the_plan_of_a_student_they_work_with(
    auth, mentor, plan, session
):
    response = auth(mentor).post(
        REVIEWS_URL,
        {"plan": str(plan.id), "status": "APPROVED", "comment": "Looks right."},
        format="json",
    )

    assert response.status_code == 201, response.data
    plan.refresh_from_db()
    assert plan.approved_by_id == mentor.id


def test_a_mentor_cannot_review_a_stranger_plan(auth, other_mentor, plan, session):
    """The hole.

    `other_mentor` has never met this learner. Nothing in the request says so —
    the plan id is simply a uuid in the body — and approving it would put their
    name on a plan they have no standing to endorse.
    """
    response = auth(other_mentor).post(
        REVIEWS_URL,
        {"plan": str(plan.id), "status": "APPROVED", "comment": "Fine by me."},
        format="json",
    )

    assert response.status_code in {400, 403, 404}, response.data
    plan.refresh_from_db()
    assert plan.approved_by_id is None, "a stranger approved this plan"


def test_a_student_cannot_review_their_own_plan(auth, student, plan):
    """Otherwise the review is a self-signed certificate."""
    response = auth(student).post(
        REVIEWS_URL,
        {"plan": str(plan.id), "status": "APPROVED"},
        format="json",
    )

    assert response.status_code in {400, 403, 404}
    plan.refresh_from_db()
    assert plan.approved_by_id is None


def test_a_mentor_only_sees_their_own_reviews(auth, mentor, other_mentor, plan, session):
    from apps.mentorship.models import PlanReview

    PlanReview.objects.create(plan=plan, reviewer=mentor, status="PENDING")

    response = auth(other_mentor).get(REVIEWS_URL)

    assert response.data["results"] == []


def test_the_learner_sees_reviews_of_their_own_plan(auth, student, mentor, plan, session):
    from apps.mentorship.models import PlanReview

    PlanReview.objects.create(
        plan=plan, reviewer=mentor, status="CHANGES_REQUESTED", comment="Add SQL."
    )

    response = auth(student).get(REVIEWS_URL)

    assert len(response.data["results"]) == 1
    assert response.data["results"][0]["comment"] == "Add SQL."
