"""Matching orchestration: what to recompute, and when."""

from __future__ import annotations

import logging

from django.db.models import Q

from apps.common.enums import ModerationStatus
from apps.jobs.models import Vacancy
from apps.profiles.models import UserSkill
from apps.taxonomy.models import Profession

from .engine import compute_and_store_match
from .models import MatchResult, MatchWeightProfile, ProfessionMatch

logger = logging.getLogger(__name__)

#: Recomputing every student against every vacancy is O(n·m) and pointless —
#: a candidate with zero overlapping skills will never rank. We only consider
#: pairs that share at least one required skill.
MAX_CANDIDATES_PER_VACANCY = 500


def mark_matches_stale(*, student=None, vacancy=None) -> int:
    """Flag results for recomputation rather than recomputing inline.

    Called from signals on skill, course, test and experience changes; keeps a
    lesson completion from turning into hundreds of match computations inside
    the request.
    """
    queryset = MatchResult.objects.filter(is_stale=False)
    if student is not None:
        queryset = queryset.filter(student=student)
    if vacancy is not None:
        queryset = queryset.filter(vacancy=vacancy)
    if student is None and vacancy is None:
        return 0
    return queryset.update(is_stale=True)


def candidate_pool_for_vacancy(vacancy: Vacancy):
    """Students worth scoring against this vacancy."""
    from apps.accounts.models import User
    from apps.common.enums import Role

    skill_ids = list(vacancy.skill_links.values_list("skill_id", flat=True))
    base = User.objects.filter(role=Role.STUDENT, is_active=True)
    if not skill_ids:
        return base.none()

    candidate_ids = (
        UserSkill.objects.filter(skill_id__in=skill_ids)
        .values_list("user_id", flat=True)
        .distinct()[:MAX_CANDIDATES_PER_VACANCY]
    )
    return base.filter(id__in=list(candidate_ids)).select_related("student_profile")


def vacancy_pool_for_student(student):
    """Open vacancies worth scoring for this student."""
    skill_ids = list(
        UserSkill.objects.filter(user=student).values_list("skill_id", flat=True)
    )
    queryset = Vacancy.objects.filter(status=ModerationStatus.PUBLISHED)
    if not skill_ids:
        return queryset.none()
    return (
        queryset.filter(skill_links__skill_id__in=skill_ids)
        .distinct()
        .select_related("employer", "region", "profession")
    )


def recompute_matches_for_student(student, *, limit: int | None = None) -> int:
    profile = MatchWeightProfile.active()
    vacancies = vacancy_pool_for_student(student)
    if limit:
        vacancies = vacancies[:limit]

    count = 0
    for vacancy in vacancies:
        try:
            compute_and_store_match(student, vacancy, weight_profile=profile)
            count += 1
        except Exception:  # pragma: no cover - one bad row must not stop the rest
            logger.exception(
                "Match computation failed for student=%s vacancy=%s",
                student.id,
                vacancy.id,
            )
    return count


def recompute_matches_for_vacancy(vacancy: Vacancy, *, limit: int | None = None) -> int:
    profile = MatchWeightProfile.active()
    students = candidate_pool_for_vacancy(vacancy)
    if limit:
        students = students[:limit]

    count = 0
    for student in students:
        try:
            compute_and_store_match(student, vacancy, weight_profile=profile)
            count += 1
        except Exception:  # pragma: no cover
            logger.exception(
                "Match computation failed for student=%s vacancy=%s",
                student.id,
                vacancy.id,
            )
    return count


def refresh_stale_matches(*, batch_size: int = 200) -> int:
    """Process the stale queue. Driven by a management command or Celery."""
    profile = MatchWeightProfile.active()
    stale = (
        MatchResult.objects.filter(is_stale=True)
        .select_related("student", "vacancy")[:batch_size]
    )
    count = 0
    for result in stale:
        try:
            compute_and_store_match(
                result.student, result.vacancy, weight_profile=profile
            )
            count += 1
        except Exception:  # pragma: no cover
            logger.exception("Stale match refresh failed for %s", result.id)
    return count


def get_match(student, vacancy, *, allow_compute: bool = True) -> MatchResult | None:
    """Fetch a match, recomputing lazily if missing or stale."""
    result = MatchResult.objects.filter(student=student, vacancy=vacancy).first()
    if result is not None and not result.is_stale:
        return result
    if not allow_compute:
        return result
    return compute_and_store_match(student, vacancy)


# ---------------------------------------------------------------------------
# Profession readiness (Career Path)
# ---------------------------------------------------------------------------
def compute_profession_match(student, profession: Profession) -> ProfessionMatch:
    from apps.profiles.services import get_skill_gap

    gap = get_skill_gap(student, profession)
    match, _created = ProfessionMatch.objects.update_or_create(
        student=student,
        profession=profession,
        defaults={
            "score": gap["readiness"],
            "matched_skills": gap["matching_skills"],
            "missing_skills": gap["missing_skills"] + gap["partial_skills"],
            "breakdown": {
                "required_total": gap["required_total"],
                "required_met": gap["required_met"],
                "partial": len(gap["partial_skills"]),
            },
            "is_stale": False,
        },
    )
    return match


def recompute_profession_matches(student, *, limit: int = 20) -> int:
    """Score a student against the professions worth suggesting.

    Restricted to professions that share a skill with the student, plus their
    declared target — scoring all of them would be noise.
    """
    skill_ids = list(
        UserSkill.objects.filter(user=student).values_list("skill_id", flat=True)
    )
    profile = getattr(student, "student_profile", None)
    target_id = getattr(profile, "target_profession_id", None)

    query = Q(skill_links__skill_id__in=skill_ids) if skill_ids else Q(pk__in=[])
    if target_id:
        query |= Q(id=target_id)

    professions = (
        Profession.objects.filter(is_active=True).filter(query).distinct()[:limit]
    )

    count = 0
    for profession in professions:
        compute_profession_match(student, profession)
        count += 1
    return count


def top_professions(student, *, limit: int = 5) -> list[ProfessionMatch]:
    matches = list(
        ProfessionMatch.objects.filter(student=student)
        .select_related("profession")
        .order_by("-score")[:limit]
    )
    if not matches:
        recompute_profession_matches(student)
        matches = list(
            ProfessionMatch.objects.filter(student=student)
            .select_related("profession")
            .order_by("-score")[:limit]
        )
    return matches
