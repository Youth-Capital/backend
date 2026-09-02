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


def test_plan_title_and_summary_are_keys(learner, profession):
    plan = create_plan_from_gap(user=learner, profession=profession)

    assert plan.title.startswith("plan.generated.title::")
    assert "days=90" in plan.title
    assert plan.summary.startswith("plan.generated.summary::")
    assert "milestones=3" in plan.summary


def test_task_descriptions_are_keys(learner, profession):
    """The screen that showed English inside a Russian page."""
    plan = create_plan_from_gap(user=learner, profession=profession)

    described = [task for task in plan.tasks.all() if task.description]
    assert described, "the generator should describe the tasks it invents"
    # Both branches, or this passes while half the generator writes prose.
    assert {task.type for task in described} >= {"COURSE", "CUSTOM"}
    for task in described:
        assert KEY.match(task.description), task.description


def test_task_titles_are_keys_or_real_course_names(learner, profession):
    """A course task carries the course's own title, which is already translated."""
    plan = create_plan_from_gap(user=learner, profession=profession)

    for task in plan.tasks.all():
        if task.type == "COURSE":
            continue
        assert KEY.match(task.title), task.title


def test_arguments_survive_the_round_trip(learner, profession):
    """The skill name has to come back out, or the sentence loses its subject."""
    plan = create_plan_from_gap(user=learner, profession=profession)

    task = plan.tasks.filter(description__startswith="plan.desc.reach_level").first()
    assert task is not None
    arguments = dict(
        part.split("=", 1) for part in task.description.split("::")[1:] if "=" in part
    )
    assert arguments["skill"] in {"SIEM", "Linux"}
    assert arguments["level"] == "55"
