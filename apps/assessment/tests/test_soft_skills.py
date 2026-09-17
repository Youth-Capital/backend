"""Soft-skill (situational judgement) assessment."""

import pytest

from apps.assessment.models import (
    AnswerOption,
    AttemptStatus,
    Question,
    QuestionType,
    Test,
)
# Imported under an alias: `TestType` matches pytest.ini's `python_classes`
# pattern, and pytest would warn on every run that it cannot collect an enum.
from apps.assessment.models import TestType as TType
from apps.assessment.services import start_attempt, submit_attempt
from apps.common.enums import EvidenceSource, ModerationStatus
from apps.profiles.models import SkillStatus, UserSkill


@pytest.fixture
def soft_category(db, taxonomy):
    """Turn the fixture taxonomy's data category into a soft-skill branch."""
    from apps.taxonomy.models import Skill, SkillCategory

    soft = SkillCategory.objects.create(
        slug="soft", name_uz="Yumshoq", is_soft_skill=True
    )
    teamwork = Skill.objects.create(
        slug="teamwork", name_uz="Jamoada ishlash", category=soft
    )
    communication = Skill.objects.create(
        slug="communication", name_uz="Kommunikatsiya", category=soft
    )
    return {"category": soft, "teamwork": teamwork, "communication": communication}


@pytest.fixture
def sjt(db, admin_user, soft_category):
    """Two situations, four weighted options each."""
    test = Test.objects.create(
        title="Soft skills",
        type=TType.SOFT_SKILL,
        author=admin_user,
        passing_score=0,
        status=ModerationStatus.PUBLISHED,
    )
    questions = {}
    for key in ("teamwork", "communication"):
        question = Question.objects.create(
            test=test,
            text=f"Situation about {key}?",
            type=QuestionType.SITUATIONAL,
            points=10,
            skill=soft_category[key],
        )
        for order, weight in enumerate((100, 60, 30, 0)):
            AnswerOption.objects.create(
                question=question,
                text=f"{key} option {weight}",
                weight=weight,
                is_correct=weight >= 70,
                order=order,
            )
        questions[key] = question
    return {"test": test, "questions": questions}


def _answer(question, weight):
    option = question.options.get(weight=weight)
    return {"question_id": str(question.id), "option_ids": [str(option.id)]}


def test_option_weight_decides_the_points(student, sjt):
    attempt = start_attempt(student, sjt["test"])
    graded = submit_attempt(
        attempt,
        [
            _answer(sjt["questions"]["teamwork"], 100),
            _answer(sjt["questions"]["communication"], 30),
        ],
    )

    # 10 of 10 on one, 3 of 10 on the other.
    assert graded.score == 13
    assert graded.max_score == 20
    assert graded.percentage == 65


def test_a_soft_skill_test_cannot_be_failed(student, sjt):
    """Every answer is the weakest one available, and it still passes.

    A soft-skill profile is a shape, not a verdict — telling a 17-year-old they
    failed at being themselves is not a product decision anyone should make.
    """
    attempt = start_attempt(student, sjt["test"])
    graded = submit_attempt(
        attempt,
        [
            _answer(sjt["questions"]["teamwork"], 0),
            _answer(sjt["questions"]["communication"], 0),
        ],
    )

    assert graded.percentage == 0
    assert graded.passed is True
    assert graded.status == AttemptStatus.GRADED


def test_per_competency_breakdown_is_weight_based(student, sjt, soft_category):
    attempt = start_attempt(student, sjt["test"])
    graded = submit_attempt(
        attempt,
        [
            _answer(sjt["questions"]["teamwork"], 100),
            _answer(sjt["questions"]["communication"], 60),
        ],
    )

    by_skill = {r.skill_id: r.percentage for r in graded.skill_results.all()}
    assert by_skill[soft_category["teamwork"].id] == 100
    assert by_skill[soft_category["communication"].id] == 60


def test_result_lands_as_soft_evidence_not_test_evidence(student, sjt, soft_category):
    """The weighting distinction the whole design rests on.

    An SJT records what someone says they would do. Filing that at TEST weight
    (0.90) would let a twenty-minute questionnaire count as much as a graded
    exam, and matching would start rewarding self-description.
    """
    attempt = start_attempt(student, sjt["test"])
    submit_attempt(attempt, [_answer(sjt["questions"]["teamwork"], 100)])

    link = UserSkill.objects.get(user=student, skill=soft_category["teamwork"])
    evidence = link.evidence.get()

    assert evidence.source == EvidenceSource.SOFT_TEST
    assert float(evidence.weight) == 0.60
    # And it must not confer verification.
    assert link.status == SkillStatus.DECLARED


def test_taking_view_never_exposes_option_weights(student, sjt, auth):
    """A student who can read the weights does not need to answer."""
    client = auth(student)
    response = client.post(f"/api/v1/assessment/tests/{sjt['test'].id}/start/")

    assert response.status_code == 201
    for question in response.data["questions"]:
        for option in question["options"]:
            assert "weight" not in option
            assert "is_correct" not in option


def test_unanswered_situation_scores_zero_rather_than_crashing(student, sjt):
    attempt = start_attempt(student, sjt["test"])
    graded = submit_attempt(attempt, [_answer(sjt["questions"]["teamwork"], 60)])

    assert graded.score == 6
    assert graded.max_score == 20


def test_multiple_selections_are_rejected(student, sjt):
    """One situation, one action. Picking every option is not an answer."""
    question = sjt["questions"]["teamwork"]
    attempt = start_attempt(student, sjt["test"])
    graded = submit_attempt(
        attempt,
        [
            {
                "question_id": str(question.id),
                "option_ids": [str(o.id) for o in question.options.all()[:2]],
            }
        ],
    )

    assert graded.score == 0


def test_submit_response_carries_the_competency_breakdown(student, sjt, auth):
    """The result screen's whole payload, checked over HTTP.

    Regression: the viewset prefetched `skill_results` before grading created
    them, so the response came back with an empty breakdown while the database
    held nine rows — an empty result screen at the exact moment the test is
    supposed to pay off.
    """
    client = auth(student)
    started = client.post(f"/api/v1/assessment/tests/{sjt['test'].id}/start/")
    attempt_id = started.data["id"]

    response = client.post(
        f"/api/v1/assessment/attempts/{attempt_id}/submit/",
        {
            "answers": [
                _answer(sjt["questions"]["teamwork"], 100),
                _answer(sjt["questions"]["communication"], 60),
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["test_type"] == "SOFT_SKILL"
    assert len(response.data["skill_results"]) == 2
    assert {row["percentage"] for row in response.data["skill_results"]} == {100, 60}
