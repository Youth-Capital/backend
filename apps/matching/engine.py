"""The matching engine.

Pure computation: no HTTP, no request object, no side effects beyond the
MatchResult it is asked to persist. That makes it testable and lets the same
code answer both questions the platform asks —

* employer: "who are the best candidates for this vacancy?"
* student:  "which vacancies fit me?"

Design notes are in docs/01-ANALYSIS.md §3.1–3.4. The short version: the six
components are chosen to be *non-overlapping*, because the prompt's original
weights counted test and course results twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from django.db import transaction

from apps.common.enums import RequirementLevel
from apps.experience.models import TENURE_TYPES, Experience
from apps.knowledge.models import KnowledgeScore
from apps.profiles.models import SkillStatus, UserSkill

from .models import MatchResult, MatchWeightProfile

#: Preferred skills count, but a missing "nice to have" must not sink a
#: candidate the way a missing hard requirement does.
PREFERRED_WEIGHT_FACTOR = 0.4

EDUCATION_RANK = {"NONE": 0, "SCHOOL": 1, "COLLEGE": 2, "UNIVERSITY": 3}
PROFILE_EDUCATION_RANK = {
    "NONE": 0,
    "SCHOOL": 1,
    "COLLEGE": 2,
    "UNIVERSITY": 3,
    "GRADUATE": 3,
}


@dataclass
class SkillAssessment:
    skill_id: str
    skill_name: str
    requirement: str
    required_level: int
    current_level: int
    knowledge_level: int
    confidence: float
    verified: bool
    weight: float
    met: bool


@dataclass
class MatchComputation:
    overall: int = 0
    coverage: int = 0
    knowledge: int = 0
    verification: int = 0
    experience: int = 0
    education: int = 0
    location: int = 0
    matched_skills: list[dict] = field(default_factory=list)
    missing_skills: list[dict] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)
    explanation: list[dict] = field(default_factory=list)


class MatchingStrategy:
    """Interface so a future ML ranker can replace the rules without touching
    callers (prompt §20: "система должна быть готова к замене rule-based
    matching на AI/ML matching")."""

    def compute(self, student, vacancy, weights: dict) -> MatchComputation:
        raise NotImplementedError


class RuleBasedMatching(MatchingStrategy):
    def compute(self, student, vacancy, weights: dict) -> MatchComputation:
        assessments = _assess_skills(student, vacancy)
        result = MatchComputation()

        coverage, knowledge, verification = _skill_components(assessments)
        experience, exp_detail = _experience_component(student, vacancy)
        education, edu_detail = _education_component(student, vacancy)
        location, loc_detail = _location_component(student, vacancy)

        result.coverage = coverage
        result.knowledge = knowledge
        result.verification = verification
        result.experience = experience
        result.education = education
        result.location = location

        overall = (
            coverage * weights.get("coverage", 0)
            + knowledge * weights.get("knowledge", 0)
            + verification * weights.get("verification", 0)
            + experience * weights.get("experience", 0)
            + education * weights.get("education", 0)
            + location * weights.get("location", 0)
        )
        result.overall = max(0, min(100, round(overall)))

        result.matched_skills = [
            _skill_payload(a) for a in assessments if a.current_level > 0
        ]
        result.missing_skills = [
            _skill_payload(a) for a in assessments if a.current_level == 0
        ]
        result.breakdown = {
            "weights": weights,
            "skills_required": sum(
                1 for a in assessments if a.requirement == RequirementLevel.REQUIRED
            ),
            "skills_met": sum(1 for a in assessments if a.met),
            "experience": exp_detail,
            "education": edu_detail,
            "location": loc_detail,
        }
        result.explanation = _build_explanation(assessments, result, exp_detail)
        return result


def _assess_skills(student, vacancy) -> list[SkillAssessment]:
    links = list(
        vacancy.skill_links.select_related("skill").order_by("order", "id")
    )
    if not links:
        return []

    skill_ids = [link.skill_id for link in links]
    user_skills = {
        us.skill_id: us
        for us in UserSkill.objects.filter(user=student, skill_id__in=skill_ids)
    }
    knowledge = {
        ks.skill_id: ks
        for ks in KnowledgeScore.objects.filter(user=student, skill_id__in=skill_ids)
    }

    assessments = []
    for link in links:
        user_skill = user_skills.get(link.skill_id)
        knowledge_score = knowledge.get(link.skill_id)
        current = user_skill.proficiency if user_skill else 0
        known = knowledge_score.score if knowledge_score else 0
        base_weight = float(link.weight)
        if link.requirement == RequirementLevel.PREFERRED:
            base_weight *= PREFERRED_WEIGHT_FACTOR

        assessments.append(
            SkillAssessment(
                skill_id=str(link.skill_id),
                skill_name=link.skill.name,
                requirement=link.requirement,
                required_level=link.min_knowledge_score,
                current_level=current,
                knowledge_level=known,
                confidence=float(user_skill.confidence) if user_skill else 0.0,
                verified=bool(user_skill and user_skill.status == SkillStatus.VERIFIED),
                weight=base_weight,
                met=current >= link.min_knowledge_score,
            )
        )
    return assessments


def _skill_components(assessments: Iterable[SkillAssessment]) -> tuple[int, int, int]:
    """Return (coverage, knowledge, verification), each 0-100.

    * coverage — weighted share of required skills the candidate has at all,
      discounted by how much the platform trusts the claim;
    * knowledge — weighted attainment against the required level, using the
      objective knowledge score where one exists;
    * verification — weighted share of skills backed by independent proof.
    """
    assessments = list(assessments)
    if not assessments:
        return 0, 0, 0

    weight_total = sum(a.weight for a in assessments) or 1.0

    coverage = sum(
        a.weight * min(1.0, a.confidence + 0.15) for a in assessments if a.current_level > 0
    )

    knowledge_sum = 0.0
    for a in assessments:
        if a.required_level <= 0:
            attainment = 1.0 if a.current_level > 0 else 0.0
        else:
            # Prefer the objective score; fall back to declared proficiency
            # when the skill has never been assessed.
            level = a.knowledge_level or a.current_level
            attainment = min(1.0, level / a.required_level)
        knowledge_sum += a.weight * attainment

    verification = sum(a.weight for a in assessments if a.verified)

    return (
        round(100 * coverage / weight_total),
        round(100 * knowledge_sum / weight_total),
        round(100 * verification / weight_total),
    )


def _experience_component(student, vacancy) -> tuple[int, dict]:
    """Relevant months of tenure against the vacancy's requirement.

    "Relevant" means weighted by how much the role's skills overlap with the
    skills attached to that experience entry — three years of unrelated work is
    not three years of relevant experience (docs/01-ANALYSIS.md §3.4).
    """
    required_months = vacancy.min_experience_months
    required_skill_ids = set(
        vacancy.skill_links.values_list("skill_id", flat=True)
    )

    entries = (
        Experience.objects.filter(user=student, type__in=TENURE_TYPES)
        .prefetch_related("skill_links")
        .only("id", "type", "start_date", "end_date", "is_current")
    )

    total_months = 0.0
    relevant_months = 0.0
    for entry in entries:
        months = entry.duration_months
        total_months += months
        if not required_skill_ids:
            relevant_months += months
            continue
        entry_skills = {link.skill_id for link in entry.skill_links.all()}
        if not entry_skills:
            # Unlabelled experience still counts, but only partially — we
            # cannot verify it is related.
            relevant_months += months * 0.3
            continue
        overlap = len(entry_skills & required_skill_ids) / len(required_skill_ids)
        relevant_months += months * max(overlap, 0.2)

    relevant_months = round(relevant_months)
    detail = {
        "required_months": required_months,
        "relevant_months": relevant_months,
        "total_months": round(total_months),
    }

    if required_months == 0:
        # No requirement: any relevant experience is a bonus, never a penalty.
        score = 100 if relevant_months > 0 else 80
    else:
        score = round(100 * min(1.0, relevant_months / required_months))
    detail["score"] = score
    return score, detail


def _education_component(student, vacancy) -> tuple[int, dict]:
    required = EDUCATION_RANK.get(vacancy.education_required, 0)
    profile = getattr(student, "student_profile", None)
    actual = PROFILE_EDUCATION_RANK.get(
        getattr(profile, "education_status", "NONE"), 0
    )
    detail = {"required": vacancy.education_required, "actual": getattr(profile, "education_status", "NONE")}
    if required == 0:
        detail["score"] = 100
        return 100, detail
    score = 100 if actual >= required else round(100 * actual / required)
    detail["score"] = score
    return score, detail


def _location_component(student, vacancy) -> tuple[int, dict]:
    from apps.jobs.models import WorkMode

    if vacancy.work_mode == WorkMode.REMOTE:
        return 100, {"mode": vacancy.work_mode, "score": 100, "reason": "remote"}

    profile = getattr(student, "student_profile", None)
    student_region = getattr(profile, "region_id", None)
    detail = {"mode": vacancy.work_mode}

    if vacancy.region_id is None or student_region is None:
        detail["score"] = 60  # unknown, not disqualifying
        detail["reason"] = "unknown_region"
        return 60, detail

    if student_region == vacancy.region_id:
        detail["score"] = 100
        detail["reason"] = "same_region"
        return 100, detail

    score = 80 if vacancy.work_mode == WorkMode.HYBRID else 40
    detail["score"] = score
    detail["reason"] = "different_region"
    return score, detail


def _skill_payload(a: SkillAssessment) -> dict:
    return {
        "skill_id": a.skill_id,
        "skill": a.skill_name,
        "requirement": a.requirement,
        "required_level": a.required_level,
        "current_level": a.current_level,
        "knowledge_level": a.knowledge_level,
        "verified": a.verified,
        "met": a.met,
    }


def _build_explanation(
    assessments: list[SkillAssessment], result: MatchComputation, exp_detail: dict
) -> list[dict]:
    """Structured, fact-based reasons.

    Returns codes plus data rather than sentences so the SPA can render them in
    Uzbek, Russian or English — and so "Why this candidate?" is auditable.
    """
    reasons: list[dict] = []

    verified = [a for a in assessments if a.verified and a.met]
    if verified:
        reasons.append(
            {
                "code": "verified_skills",
                "sentiment": "positive",
                "data": {
                    "skills": [a.skill_name for a in verified[:5]],
                    "count": len(verified),
                },
            }
        )

    declared_only = [
        a for a in assessments if a.current_level > 0 and not a.verified and a.met
    ]
    if declared_only:
        reasons.append(
            {
                "code": "declared_not_verified",
                "sentiment": "neutral",
                "data": {"skills": [a.skill_name for a in declared_only[:5]]},
            }
        )

    below = [
        a
        for a in assessments
        if a.current_level > 0 and not a.met and a.requirement == RequirementLevel.REQUIRED
    ]
    if below:
        reasons.append(
            {
                "code": "below_required_level",
                "sentiment": "negative",
                "data": {
                    "skills": [
                        {
                            "skill": a.skill_name,
                            "current": a.current_level,
                            "required": a.required_level,
                        }
                        for a in below[:5]
                    ]
                },
            }
        )

    missing_required = [
        a
        for a in assessments
        if a.current_level == 0 and a.requirement == RequirementLevel.REQUIRED
    ]
    if missing_required:
        reasons.append(
            {
                "code": "missing_required_skills",
                "sentiment": "negative",
                "data": {"skills": [a.skill_name for a in missing_required[:5]]},
            }
        )

    if exp_detail["required_months"]:
        reasons.append(
            {
                "code": (
                    "experience_met"
                    if exp_detail["relevant_months"] >= exp_detail["required_months"]
                    else "experience_short"
                ),
                "sentiment": (
                    "positive"
                    if exp_detail["relevant_months"] >= exp_detail["required_months"]
                    else "negative"
                ),
                "data": exp_detail,
            }
        )
    elif exp_detail["relevant_months"]:
        reasons.append(
            {
                "code": "experience_bonus",
                "sentiment": "positive",
                "data": exp_detail,
            }
        )

    return reasons


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_strategy = RuleBasedMatching()


def compute_match(student, vacancy, *, weight_profile=None) -> MatchComputation:
    """Compute without persisting — used by previews and tests."""
    profile = weight_profile or MatchWeightProfile.active()
    return _strategy.compute(student, vacancy, profile.normalised_weights())


@transaction.atomic
def compute_and_store_match(student, vacancy, *, weight_profile=None) -> MatchResult:
    profile = weight_profile or MatchWeightProfile.active()
    computation = _strategy.compute(student, vacancy, profile.normalised_weights())

    result, _created = MatchResult.objects.update_or_create(
        student=student,
        vacancy=vacancy,
        defaults={
            "overall_score": computation.overall,
            "coverage_score": computation.coverage,
            "knowledge_score": computation.knowledge,
            "verification_score": computation.verification,
            "experience_score": computation.experience,
            "education_score": computation.education,
            "location_score": computation.location,
            "matched_skills": computation.matched_skills,
            "missing_skills": computation.missing_skills,
            "breakdown": computation.breakdown,
            "explanation": computation.explanation,
            "weight_profile": profile,
            "is_stale": False,
        },
    )
    return result
