"""Matching engine behaviour.

These tests exist because a scoring engine that is merely plausible is
worthless — the numbers have to mean something specific and stay stable.
"""

import pytest

from apps.common.enums import EvidenceSource
from apps.matching.engine import compute_match
from apps.matching.models import MatchWeightProfile

pytestmark = pytest.mark.django_db


def test_perfect_candidate_scores_high(student, vacancy, taxonomy, give_skill):
    give_skill(student, taxonomy["sql"], 90, EvidenceSource.TEST)
    give_skill(student, taxonomy["power_bi"], 85, EvidenceSource.TEST)

    result = compute_match(student, vacancy)

    assert result.overall >= 80
    assert result.coverage >= 90
    assert result.verification >= 90
    assert not result.missing_skills


def test_candidate_without_skills_scores_low(student, vacancy):
    result = compute_match(student, vacancy)

    assert result.overall <= 30
    assert result.coverage == 0
    assert len(result.missing_skills) == 2


def test_verified_beats_self_declared_at_equal_level(
    student, other_student, vacancy, taxonomy, give_skill
):
    """The core anti-inflation rule (docs/01-ANALYSIS.md §3.3).

    Two candidates claim the same level. The one who proved it must rank
    higher, or self-reporting becomes the optimal strategy.
    """
    give_skill(student, taxonomy["sql"], 80, EvidenceSource.TEST)
    give_skill(student, taxonomy["power_bi"], 80, EvidenceSource.TEST)

    give_skill(other_student, taxonomy["sql"], 80, EvidenceSource.SELF)
    give_skill(other_student, taxonomy["power_bi"], 80, EvidenceSource.SELF)

    verified = compute_match(student, vacancy)
    declared = compute_match(other_student, vacancy)

    assert verified.overall > declared.overall
    assert verified.verification > declared.verification


def test_partial_skill_scores_between(student, vacancy, taxonomy, give_skill):
    """Having a skill below the bar beats not having it, and loses to meeting it."""
    give_skill(student, taxonomy["sql"], 30, EvidenceSource.TEST)
    below = compute_match(student, vacancy)

    give_skill(student, taxonomy["sql"], 90, EvidenceSource.TEST)
    at_level = compute_match(student, vacancy)

    assert 0 < below.overall < at_level.overall


def test_preferred_skill_weighs_less_than_required(
    student, vacancy, taxonomy, give_skill
):
    from apps.common.enums import RequirementLevel
    from apps.jobs.models import VacancySkill

    VacancySkill.objects.create(
        vacancy=vacancy,
        skill=taxonomy["python"],
        requirement=RequirementLevel.PREFERRED,
        min_knowledge_score=50,
    )

    give_skill(student, taxonomy["python"], 90, EvidenceSource.TEST)
    preferred_only = compute_match(student, vacancy)

    from apps.profiles.services import remove_declared_skill  # noqa: F401
    from apps.profiles.models import UserSkill

    UserSkill.objects.filter(user=student).delete()
    give_skill(student, taxonomy["sql"], 90, EvidenceSource.TEST)
    required_only = compute_match(student, vacancy)

    assert required_only.overall > preferred_only.overall


def test_explanation_is_fact_based(student, vacancy, taxonomy, give_skill):
    """A bare percentage is not an explanation (TZ §13, prompt §19)."""
    give_skill(student, taxonomy["sql"], 90, EvidenceSource.TEST)

    result = compute_match(student, vacancy)
    codes = {reason["code"] for reason in result.explanation}

    assert "verified_skills" in codes
    assert "missing_required_skills" in codes
    for reason in result.explanation:
        assert reason["sentiment"] in {"positive", "neutral", "negative"}
        assert reason["data"]


def test_matching_is_deterministic(student, vacancy, taxonomy, give_skill):
    give_skill(student, taxonomy["sql"], 72, EvidenceSource.TEST)

    first = compute_match(student, vacancy)
    second = compute_match(student, vacancy)

    assert first.overall == second.overall
    assert first.breakdown["skills_met"] == second.breakdown["skills_met"]


def test_weights_are_configurable(student, vacancy, taxonomy, give_skill):
    """prompt §20: "сделай веса конфигурируемыми"."""
    give_skill(student, taxonomy["sql"], 90, EvidenceSource.TEST)
    give_skill(student, taxonomy["power_bi"], 90, EvidenceSource.TEST)

    baseline = compute_match(student, vacancy)

    coverage_only = MatchWeightProfile.objects.create(
        name="coverage-only",
        weights={
            "coverage": 1.0,
            "knowledge": 0,
            "verification": 0,
            "experience": 0,
            "education": 0,
            "location": 0,
        },
    )
    skewed = compute_match(student, vacancy, weight_profile=coverage_only)

    assert skewed.overall != baseline.overall
    assert skewed.overall == skewed.coverage


def test_weights_are_normalised_even_if_they_do_not_sum_to_one():
    profile = MatchWeightProfile(
        name="broken", weights={"coverage": 5, "knowledge": 5}
    )
    normalised = profile.normalised_weights()

    assert pytest.approx(sum(normalised.values())) == 1.0


def test_unlabelled_experience_counts_partially(student, vacancy, taxonomy, give_skill):
    """Experience with no skills attached cannot be verified as relevant, so it
    counts for something but not for everything."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.experience.models import Experience, ExperienceSkill, ExperienceType

    vacancy.min_experience_months = 12
    vacancy.save(update_fields=["min_experience_months"])
    give_skill(student, taxonomy["sql"], 80, EvidenceSource.TEST)

    today = timezone.localdate()
    unlabelled = Experience.objects.create(
        user=student,
        type=ExperienceType.WORK,
        title="Unrelated job",
        start_date=today - timedelta(days=365),
        end_date=today,
    )
    vague = compute_match(student, vacancy)

    ExperienceSkill.objects.create(experience=unlabelled, skill=taxonomy["sql"])
    relevant = compute_match(student, vacancy)

    assert relevant.experience > vague.experience
