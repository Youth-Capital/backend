"""Generated plan text must be translatable, not English prose.

A plan is written once and read for ninety days. It used to be generated as
finished English sentences, so a student reading the interface in Russian saw
"Reach level 55 in SIEM" under a Russian heading. These tests pin the shape
that fixes it: the server stores a key with named arguments, and the client
turns that into a sentence in whatever language is active at the time.
"""

import re

import pytest

from apps.common.enums import Role
from apps.idp.services import create_plan_from_gap
from apps.profiles.models import StudentProfile
from apps.learning.models import Course
from apps.taxonomy.models import Profession, Skill, SkillCategory

pytestmark = pytest.mark.django_db

#: Anything a person will read must be a key, or a title someone typed.
KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+(?:::|$)")


@pytest.fixture
def learner(django_user_model):
    user = django_user_model.objects.create_user(
        email="plan-text@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    StudentProfile.objects.create(user=user, youth_id="YK-PLAN-1", profile_completion=40)
    return user


@pytest.fixture
def profession():
    """Two required skills, one of which a published course covers.

    The course matters: the generator writes a different description for a
    skill it can point at a course for, and a fixture without one leaves that
    branch untested — which is exactly how the English prose survived a green
    suite the first time.
    """
    category = SkillCategory.objects.create(name_uz="Xavfsizlik", slug="sec")
    profession = Profession.objects.create(name_uz="Xavfsizlik mutaxassisi", slug="sec-pro")
    skills = []
    for index, name in enumerate(("SIEM", "Linux")):
        skill = Skill.objects.create(name_uz=name, slug=f"s{index}", category=category)
        profession.skill_links.create(skill=skill, min_proficiency=55, requirement="REQUIRED")
        skills.append(skill)

    course = Course.objects.create(
        slug="linux-basics", title="Linux Basics", status="PUBLISHED"
    )
    course.skill_links.create(skill=skills[1])
    return profession


"""A plan keeps its shape at any length.

The milestones were fixed calendar offsets — day 30, 60 and 90 — and only the
ones that fitted inside the period were created. Nobody noticed while every
plan was 90 days. Measured before the change: a 30-day plan got one milestone
and a 15-day plan got none at all, plus one task fewer, because the tasks hang
off the milestones.
"""


@pytest.mark.parametrize("days", [14, 15, 30, 45, 90, 180, 365])
def test_every_length_gets_all_three_phases(learner, profession, days):
    plan = create_plan_from_gap(user=learner, profession=profession, period_days=days)

    milestones = list(plan.milestones.order_by("order"))
    assert len(milestones) == 3, f"{days} days gave {len(milestones)} milestones"

    due = [m.due_date for m in milestones]
    assert due == sorted(due), "phases must not share a day or go backwards"
    assert len(set(due)) == 3
    assert due[-1] == plan.end_date, "the last phase ends when the plan does"
    assert plan.tasks.count() > 0


def test_ninety_days_still_lands_on_thirty_sixty_ninety(learner, profession):
    """The change must not move the plan everybody already has."""
    from apps.idp.services import milestone_offsets

    assert [day for _, day in milestone_offsets(90)] == [30, 60, 90]


def test_the_shortest_allowed_period_still_separates_the_phases(learner, profession):
    from apps.idp.services import milestone_offsets

    days = [day for _, day in milestone_offsets(14)]

    assert days == sorted(set(days))
    assert days[-1] == 14
    assert all(1 <= day <= 14 for day in days)


def test_the_period_reaches_the_plan_through_the_api(auth, student, profession):
    """The endpoint honours period_days rather than always building 90."""
    from apps.profiles.models import StudentProfile

    StudentProfile.objects.filter(user=student).update(target_profession=profession)

    response = auth(student).post(
        "/api/v1/plan/plans/generate/", {"period_days": 15}, format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["period_days"] == 15
    assert len(response.data["milestones"]) == 3


@pytest.mark.parametrize("bad", [13, 366])
def test_a_period_outside_the_allowed_range_is_refused(auth, student, profession, bad):
    from apps.profiles.models import StudentProfile

    StudentProfile.objects.filter(user=student).update(target_profession=profession)

    response = auth(student).post(
        "/api/v1/plan/plans/generate/", {"period_days": bad}, format="json"
    )

    assert response.status_code == 400, response.data
