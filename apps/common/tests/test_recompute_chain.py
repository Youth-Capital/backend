"""The cross-module ripple required by prompt §32.

Finishing a lesson must reach the vacancy match score. If any link breaks the
product silently becomes "a set of unconnected pages", which is exactly what
the brief forbids.
"""

import pytest

from apps.common.enums import EvidenceSource, ModerationStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def course(db, taxonomy, employer):
    from apps.learning.models import Course, CourseModule, CourseSkill, Lesson

    course = Course.objects.create(
        slug="sql-course",
        title="SQL for Data Analysis",
        author=employer,
        employer=employer.employer_profile,
        category=taxonomy["sql"].category,
        status=ModerationStatus.PUBLISHED,
        duration_minutes=120,
        is_certified=True,
    )
    CourseSkill.objects.create(course=course, skill=taxonomy["sql"], target_proficiency=70)
    module = CourseModule.objects.create(course=course, title="Basics", order=0)
    for index in range(2):
        Lesson.objects.create(module=module, title=f"Lesson {index}", order=index)
    return course


def test_completing_a_course_updates_the_whole_chain(
    student, course, taxonomy, vacancy
):
    from apps.capital.models import CapitalIndex
    from apps.knowledge.models import KnowledgeScore
    from apps.learning.models import EnrollmentStatus, Lesson
    from apps.learning.services import complete_lesson, enroll
    from apps.matching.engine import compute_and_store_match
    from apps.profiles.models import SkillStatus, UserSkill

    before = compute_and_store_match(student, vacancy).overall_score

    enrollment = enroll(student, course)
    for lesson in Lesson.objects.filter(module__course=course):
        enrollment = complete_lesson(student, lesson)

    assert enrollment.status == EnrollmentStatus.COMPLETED
    assert enrollment.progress == 100

    # 1. Course completion became skill evidence.
    user_skill = UserSkill.objects.get(user=student, skill=taxonomy["sql"])
    assert user_skill.proficiency > 0
    assert user_skill.best_source == EvidenceSource.COURSE

    # 2. Knowledge score exists (course completion is objective evidence).
    assert KnowledgeScore.objects.filter(user=student, skill=taxonomy["sql"]).exists()

    # 3. Capital index was recomputed on the mapped axis.
    digital = CapitalIndex.objects.get(user=student, dimension=taxonomy["digital"])
    assert digital.score > 0

    # 4. A certificate was issued for a certified course.
    assert student.certificates.filter(course=course).exists()

    # 5. The match score moved.
    after = compute_and_store_match(student, vacancy).overall_score
    assert after > before

    # A course alone does not make a skill "verified" — only a test or a human does.
    assert user_skill.status == SkillStatus.DECLARED


def test_completing_a_course_closes_the_matching_plan_task(student, course, taxonomy):
    from apps.idp.models import Task, TaskStatus, TaskType
    from apps.learning.models import Lesson
    from apps.learning.services import complete_lesson, enroll

    task = Task.objects.create(
        user=student,
        title="Finish the SQL course",
        type=TaskType.COURSE,
        ref_type="Course",
        ref_id=course.id,
    )

    enroll(student, course)
    for lesson in Lesson.objects.filter(module__course=course):
        complete_lesson(student, lesson)

    task.refresh_from_db()
    assert task.status == TaskStatus.DONE


def test_passing_a_test_verifies_the_skill(student, taxonomy, employer):
    from apps.assessment.models import (
        AnswerOption,
        Question,
        QuestionType,
        Test,
        TestType,
    )
    from apps.assessment.services import start_attempt, submit_attempt
    from apps.profiles.models import SkillStatus, UserSkill

    test = Test.objects.create(
        title="SQL check",
        type=TestType.SKILL_TEST,
        author=employer,
        passing_score=50,
        status=ModerationStatus.PUBLISHED,
    )
    question = Question.objects.create(
        test=test, text="Q1", type=QuestionType.SINGLE, points=1, skill=taxonomy["sql"]
    )
    right = AnswerOption.objects.create(question=question, text="right", is_correct=True)
    AnswerOption.objects.create(question=question, text="wrong", is_correct=False)

    attempt = start_attempt(student, test)
    attempt = submit_attempt(
        attempt, [{"question_id": str(question.id), "option_ids": [str(right.id)]}]
    )

    assert attempt.passed
    assert attempt.percentage == 100

    user_skill = UserSkill.objects.get(user=student, skill=taxonomy["sql"])
    assert user_skill.status == SkillStatus.VERIFIED
    assert user_skill.best_source == EvidenceSource.TEST


def test_employer_screening_test_does_not_touch_the_global_profile(
    student, taxonomy, employer
):
    """docs/01-ANALYSIS.md §3.2 — the conflict-of-interest guard."""
    from apps.assessment.models import (
        AnswerOption,
        Question,
        QuestionType,
        Test,
        TestType,
    )
    from apps.assessment.services import start_attempt, submit_attempt
    from apps.knowledge.models import KnowledgeScore
    from apps.profiles.models import UserSkill

    screening = Test.objects.create(
        title="Our internal screening",
        type=TestType.SCREENING,
        author=employer,
        employer=employer.employer_profile,
        passing_score=50,
        status=ModerationStatus.PUBLISHED,
    )
    question = Question.objects.create(
        test=screening,
        text="Q1",
        type=QuestionType.SINGLE,
        points=1,
        skill=taxonomy["sql"],
    )
    right = AnswerOption.objects.create(question=question, text="right", is_correct=True)

    attempt = start_attempt(student, screening)
    submit_attempt(
        attempt, [{"question_id": str(question.id), "option_ids": [str(right.id)]}]
    )

    assert not UserSkill.objects.filter(user=student, skill=taxonomy["sql"]).exists()
    assert not KnowledgeScore.objects.filter(user=student, skill=taxonomy["sql"]).exists()


def test_unmoderated_test_does_not_touch_the_global_profile(
    student, taxonomy, employer
):
    from apps.assessment.models import Question, QuestionType, Test, TestType
    from apps.assessment.models import AnswerOption
    from apps.assessment.services import start_attempt
    from apps.common.exceptions import NotAllowed

    draft = Test.objects.create(
        title="Unreviewed test",
        type=TestType.SKILL_TEST,
        author=employer,
        status=ModerationStatus.DRAFT,
    )
    question = Question.objects.create(
        test=draft, text="Q1", type=QuestionType.SINGLE, skill=taxonomy["sql"]
    )
    AnswerOption.objects.create(question=question, text="right", is_correct=True)

    with pytest.raises(NotAllowed):
        start_attempt(student, draft)


def test_self_declared_skill_never_creates_a_knowledge_score(
    student, taxonomy
):
    """Knowledge Score is an assessment, not a claim (apps/knowledge/models.py)."""
    from apps.common.recompute import recompute_for_user
    from apps.knowledge.models import KnowledgeScore
    from apps.profiles.services import declare_skill

    declare_skill(user=student, skill=taxonomy["sql"], proficiency=95)
    recompute_for_user(student, reason="test")

    assert not KnowledgeScore.objects.filter(user=student, skill=taxonomy["sql"]).exists()


def test_capital_axis_without_data_is_reported_honestly(student, taxonomy):
    from apps.capital.services import get_capital_overview

    overview = get_capital_overview(student)
    by_slug = {d["slug"]: d for d in overview["dimensions"]}

    assert by_slug["KNOWLEDGE"]["has_data"] is False
    assert overview["measured_axes"] == 0
