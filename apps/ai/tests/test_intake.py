"""The intake interview.

What matters here is that branching actually branches, that the interview
cannot be finished half-answered, and that what a person says about
themselves lands as a *claim* rather than as proof.
"""

import pytest
from rest_framework.test import APIClient

from apps.ai.intake import BY_ID, InterviewState, validate_answer
from apps.ai.intake_service import complete, get_or_start, skip, submit
from apps.ai.models import IntakeSession
from apps.common.enums import EvidenceSource, Role
from apps.profiles.models import EducationStatus, SkillEvidence, StudentProfile
from apps.taxonomy.models import Profession, Skill, SkillCategory

pytestmark = pytest.mark.django_db


@pytest.fixture
def categories():
    return {
        slug: SkillCategory.objects.create(name_uz=slug.title(), slug=slug)
        for slug in ("data", "programming", "design")
    }


@pytest.fixture
def skill(categories):
    return Skill.objects.create(name_uz="SQL", slug="sql", category=categories["data"])


@pytest.fixture
def student(django_user_model):
    user = django_user_model.objects.create_user(
        email="new@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    StudentProfile.objects.create(user=user, youth_id="YK-TEST-0001")
    return user


def _answer_all(session, skill=None, *, knows="YES", profession=None):
    """Walk the interview the way a client would, one question at a time."""
    answers = {
        "about": "Men dasturlashni o'rganyapman.",
        "education_status": "UNIVERSITY",
        "institution": "TATU",
        "field_of_study": "Computer science",
        "goals": ["FIND_JOB", "LEARN_NEW"],
        "interests": ["data", "programming"],
        "knows_profession": knows,
        "target_profession": str(profession.id) if profession else "",
        "skills": [{"skill": str(skill.id), "level": 55}] if skill else [],
        "experience_kinds": ["PROJECT"],
        "hours_per_week": "3_7",
    }

    guard = 0
    while True:
        guard += 1
        assert guard < 40, "interview did not terminate"
        state = InterviewState(answers=dict(session.answers or {}))
        question = state.next_question()
        if question is None:
            return session
        submit(session, question.id, answers.get(question.id, ""))


def test_branching_skips_questions_that_do_not_apply(student):
    session = get_or_start(student)
    submit(session, "about", "hello")
    submit(session, "education_status", "NONE")

    state = InterviewState(answers=session.answers)
    ids = {question.id for question in state.applicable()}

    # Someone who is not studying is never asked where they study.
    assert "institution" not in ids
    assert "field_of_study" not in ids


def test_branching_includes_study_questions_for_a_student(student):
    session = get_or_start(student)
    submit(session, "about", "hello")
    submit(session, "education_status", "UNIVERSITY")

    ids = {q.id for q in InterviewState(answers=session.answers).applicable()}

    assert "institution" in ids
    assert "field_of_study" in ids


def test_target_profession_is_only_asked_when_they_know(student):
    session = get_or_start(student)
    for question_id, value in (
        ("about", ""),
        ("education_status", "GRADUATE"),
        ("field_of_study", ""),
        ("goals", ["FIND_JOB"]),
        ("interests", ["data"]),
        ("knows_profession", "NO"),
    ):
        submit(session, question_id, value)

    ids = {q.id for q in InterviewState(answers=session.answers).applicable()}
    assert "target_profession" not in ids


def test_progress_total_never_grows(student):
    """A progress bar that moves backwards reads as the app losing your work.

    Before this, an unanswered branch was excluded from the denominator, so the
    interview opened at "0 of 8" and became "2 of 10" the moment someone said
    they were at university.
    """
    session = get_or_start(student)
    totals = []

    for question_id, value in (
        ("about", ""),
        ("education_status", "UNIVERSITY"),
        ("institution", "TATU"),
        ("field_of_study", "CS"),
        ("goals", ["FIND_JOB"]),
        ("interests", ["data"]),
        ("knows_profession", "YES"),
    ):
        submit(session, question_id, value)
        _answered, total = InterviewState(answers=session.answers).progress()
        totals.append(total)

    assert totals == sorted(totals, reverse=True), totals


def test_closing_a_branch_shrinks_the_total(student):
    session = get_or_start(student)
    submit(session, "about", "")
    _before, total_before = InterviewState(answers=session.answers).progress()

    submit(session, "education_status", "NONE")
    answered, total_after = InterviewState(answers=session.answers).progress()

    assert answered == 2
    # Neither study question can now be asked, so both leave the denominator.
    assert total_after == total_before - 2
    assert total_after < len(BY_ID)


def test_cannot_complete_an_unfinished_interview(student):
    session = get_or_start(student)
    submit(session, "about", "hello")

    with pytest.raises(ValueError, match="interview_incomplete"):
        complete(session)


def test_completion_writes_the_profile(student, skill, categories):
    profession = Profession.objects.create(name_uz="Data Analyst", slug="data-analyst")
    session = _answer_all(get_or_start(student), skill, profession=profession)

    complete(session)

    profile = StudentProfile.objects.get(user=student)
    assert profile.education_status == EducationStatus.UNIVERSITY
    assert profile.institution == "TATU"
    assert profile.target_profession_id == profession.id
    assert profile.bio.startswith("Men dasturlashni")
    assert {c.slug for c in profile.interests.all()} == {"data", "programming"}


def test_declared_skills_are_self_evidence_not_proof(student, skill):
    """An interview is a claim. It must never carry more weight than that."""
    session = _answer_all(get_or_start(student), skill)
    complete(session)

    evidence = SkillEvidence.objects.get(user_skill__user=student)

    assert evidence.source == EvidenceSource.SELF
    assert float(evidence.weight) == pytest.approx(0.35)
    # Tagged with the interview so a re-run updates rather than duplicates.
    assert evidence.ref_type == "IntakeSession"
    assert str(evidence.ref_id) == str(session.id)


def test_bio_is_never_overwritten(student, skill):
    profile = StudentProfile.objects.get(user=student)
    profile.bio = "Written by hand earlier."
    profile.save(update_fields=["bio"])

    session = _answer_all(get_or_start(student), skill)
    complete(session)

    profile.refresh_from_db()
    assert profile.bio == "Written by hand earlier."


def test_not_sure_gets_profession_suggestions(student, skill, categories):
    from apps.taxonomy.models import ProfessionSkill

    profession = Profession.objects.create(name_uz="Data Analyst", slug="data-analyst")
    ProfessionSkill.objects.create(profession=profession, skill=skill, min_proficiency=50)

    session = _answer_all(get_or_start(student), skill, knows="NO")
    complete(session)

    suggestions = session.applied["suggested_professions"]
    assert suggestions
    assert suggestions[0]["name"] == "Data Analyst"
    assert suggestions[0]["overlap"] == 100


def test_only_one_active_interview_per_user(student):
    first = get_or_start(student)
    second = get_or_start(student)

    assert first.id == second.id
    assert IntakeSession.objects.filter(user=student).count() == 1


def test_required_questions_cannot_be_skipped(student):
    session = get_or_start(student)

    with pytest.raises(ValueError, match="question_required"):
        skip(session, "education_status")


def test_optional_questions_can_be_skipped(student):
    session = get_or_start(student)
    skip(session, "about")

    assert "about" in session.answers


def test_invalid_choice_is_refused(student):
    question = BY_ID["education_status"]

    with pytest.raises(ValueError, match="invalid_choice"):
        validate_answer(question, "PHD")


def test_skill_levels_are_clamped():
    question = BY_ID["skills"]

    cleaned = validate_answer(
        question, [{"skill": "abc", "level": 5000}, {"skill": "def", "level": -10}]
    )

    assert cleaned[0]["level"] == 100
    assert cleaned[1]["level"] == 0


def test_answering_an_inapplicable_question_is_refused(student):
    session = get_or_start(student)
    submit(session, "about", "")
    submit(session, "education_status", "NONE")

    with pytest.raises(ValueError, match="question_not_applicable"):
        submit(session, "institution", "Somewhere")


def test_api_walks_the_interview(student):
    client = APIClient()
    client.force_authenticate(user=student)

    response = client.get("/api/v1/ai/intake/")
    assert response.status_code == 200
    assert response.json()["question"]["id"] == "about"

    response = client.post(
        "/api/v1/ai/intake/",
        {"action": "answer", "question": "about", "value": "hi"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["question"]["id"] == "education_status"
    assert response.json()["answered"] == 1


def test_api_refuses_an_unknown_question(student):
    client = APIClient()
    client.force_authenticate(user=student)

    response = client.post(
        "/api/v1/ai/intake/",
        {"action": "answer", "question": "favourite_colour", "value": "blue"},
        format="json",
    )

    assert response.status_code == 400


def test_employers_cannot_take_the_learner_interview(django_user_model):
    employer = django_user_model.objects.create_user(
        email="hr@example.com", password="Str0ng!passw0rd", role=Role.EMPLOYER
    )
    client = APIClient()
    client.force_authenticate(user=employer)

    assert client.get("/api/v1/ai/intake/").status_code == 403
