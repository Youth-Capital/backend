"""Combined hard + soft capability analysis."""

import pytest

from apps.ai.capability import MIN_COMPETENCIES, build_capability_report
from apps.common.enums import EvidenceSource


@pytest.fixture
def soft_skills(db):
    from apps.taxonomy.models import Skill, SkillCategory

    category = SkillCategory.objects.create(
        slug="soft", name_uz="Yumshoq", is_soft_skill=True
    )
    return [
        Skill.objects.create(slug=slug, name_uz=slug, category=category)
        for slug in (
            "teamwork",
            "communication",
            "leadership",
            "adaptability",
            "problem-solving",
        )
    ]


def _rate(user, skill, score, source=EvidenceSource.SOFT_TEST):
    from apps.profiles.services import record_skill_evidence

    record_skill_evidence(user=user, skill=skill, source=source, score=score)


def test_too_few_competencies_reports_no_profile(student, taxonomy, soft_skills):
    """Three answers is not a personality profile, and saying so is the
    feature."""
    _rate(student, soft_skills[0], 80)
    _rate(student, soft_skills[1], 75)

    report = build_capability_report(student)

    assert report.balance == "insufficient_data"
    assert any(n["code"] == "capability.take_soft_test" for n in report.notes)
    assert report.soft["competency_count"] < MIN_COMPETENCIES


def test_hard_and_soft_are_reported_separately(student, taxonomy, soft_skills):
    for skill in soft_skills:
        _rate(student, skill, 80)
    _rate(student, taxonomy["sql"], 85, EvidenceSource.TEST)
    _rate(student, taxonomy["python"], 80, EvidenceSource.TEST)

    report = build_capability_report(student)

    assert report.soft["competency_count"] == len(soft_skills)
    assert report.hard["skill_count"] == 2
    # Never merged into one number.
    assert report.hard["average"] != report.soft["average"] or report.balance
    soft_names = {row["skill"] for row in report.soft["competencies"]}
    hard_names = {row["skill"] for row in report.hard["top"]}
    assert not (soft_names & hard_names)


def test_technical_ahead_of_behavioural_is_named(student, taxonomy, soft_skills):
    for skill in soft_skills:
        _rate(student, skill, 30)
    for skill in (taxonomy["sql"], taxonomy["python"], taxonomy["power_bi"]):
        _rate(student, skill, 95, EvidenceSource.EMPLOYER)

    report = build_capability_report(student)

    assert report.balance == "hard_led"
    assert any(n["code"] == "capability.hard_ahead_of_soft" for n in report.notes)
    # And it must say *which* competencies, not only that the average is low.
    assert report.soft["weakest"]
    assert any(a["code"] == "action.develop_competency" for a in report.next_actions)


def test_behavioural_ahead_of_technical_is_named(student, taxonomy, soft_skills):
    for skill in soft_skills:
        _rate(student, skill, 90)
    _rate(student, taxonomy["sql"], 20, EvidenceSource.SELF)

    report = build_capability_report(student)

    assert report.balance == "soft_led"
    assert any(n["code"] == "capability.soft_ahead_of_hard" for n in report.notes)


def test_self_reported_soft_profile_is_flagged_as_such(student, taxonomy, soft_skills):
    """An employer reading this is entitled to know it is a self-report."""
    for skill in soft_skills:
        _rate(student, skill, 75)
    _rate(student, taxonomy["sql"], 70, EvidenceSource.TEST)

    report = build_capability_report(student)

    assert all(row["self_reported"] for row in report.soft["competencies"])
    assert any(n["code"] == "capability.soft_all_self_reported" for n in report.notes)


def test_externally_confirmed_competency_is_not_self_reported(
    student, taxonomy, soft_skills
):
    for skill in soft_skills:
        _rate(student, skill, 70)
    _rate(student, soft_skills[0], 90, EvidenceSource.EMPLOYER)
    _rate(student, taxonomy["sql"], 70, EvidenceSource.TEST)

    report = build_capability_report(student)

    assessed = next(
        row
        for row in report.soft["competencies"]
        if row["skill"] == soft_skills[0].name
    )
    assert assessed["self_reported"] is False
    assert not any(
        n["code"] == "capability.soft_all_self_reported" for n in report.notes
    )


def test_endpoint_serves_the_student_their_own_report(student, soft_skills, auth):
    for skill in soft_skills:
        _rate(student, skill, 70)

    response = auth(student).get("/api/v1/ai/capability/")

    assert response.status_code == 200
    assert set(response.data) >= {"hard", "soft", "balance", "notes", "next_actions"}


def test_employer_cannot_read_a_stranger(employer, other_student, auth):
    """Without this the endpoint is a profile reader for any id typed in."""
    response = auth(employer).get(f"/api/v1/ai/capability/?user={other_student.id}")

    assert response.status_code == 403


def test_employer_can_read_a_candidate_for_their_vacancy(
    employer, student, vacancy, taxonomy, give_skill, auth
):
    from apps.matching.services import recompute_matches_for_student

    give_skill(student, taxonomy["sql"], 80)
    give_skill(student, taxonomy["power_bi"], 70)
    recompute_matches_for_student(student, limit=10)

    response = auth(employer).get(f"/api/v1/ai/capability/?user={student.id}")

    assert response.status_code == 200
    assert response.data["user_id"] == str(student.id)


def test_student_cannot_read_another_student(student, other_student, auth):
    response = auth(student).get(f"/api/v1/ai/capability/?user={other_student.id}")

    assert response.status_code == 403
