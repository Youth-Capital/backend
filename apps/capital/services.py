"""Capital index computation.

Each axis blends two things:

1. **Skill component** — the confidence-weighted proficiency of skills mapped
   to that axis through ``taxonomy.SkillDimension``.
2. **Activity component** — behavioural signals the platform can actually
   observe (finished courses, verified experience, mentor sessions, volunteer
   work), capped so nobody reaches 100 by volume alone.

Axes with no observable signal yet (HEALTH has no data source in the MVP) are
reported honestly with ``has_data: false`` rather than shown as a fake zero or
quietly padded — an index nobody can explain is worse than a missing one.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.profiles.models import SkillStatus, UserSkill
from apps.taxonomy.models import CapitalDimension, CapitalDimensionSlug, SkillDimension

from .models import CapitalIndex, CapitalSnapshot, CapitalWeightConfig

#: Axis -> how to observe progress from other modules.
#: Each entry: (callable name, points per unit, cap).
ACTIVITY_SIGNALS: dict[str, list[tuple[str, int, int]]] = {
    CapitalDimensionSlug.KNOWLEDGE: [("completed_courses", 12, 60), ("passed_tests", 8, 40)],
    CapitalDimensionSlug.PROFESSIONAL: [
        ("work_experience_months", 3, 60),
        ("verified_skills", 6, 40),
    ],
    CapitalDimensionSlug.DIGITAL_AI: [("completed_courses", 10, 50)],
    CapitalDimensionSlug.SOCIAL: [
        ("mentor_sessions", 15, 60),
        ("recommendations", 10, 40),
    ],
    CapitalDimensionSlug.ENTREPRENEURIAL: [
        ("projects", 15, 60),
        ("competitions", 12, 40),
    ],
    CapitalDimensionSlug.FINANCIAL: [("completed_courses", 12, 60)],
    CapitalDimensionSlug.PERSONAL_ETHICAL: [
        ("completed_tasks", 3, 60),
        ("mentor_sessions", 8, 40),
    ],
    CapitalDimensionSlug.HEALTH: [],
    CapitalDimensionSlug.CIVIC: [("volunteer_experience", 20, 100)],
}


def _collect_activity_counters(user) -> dict[str, int]:
    """Gather every raw counter once, so nine axes do not run nine query sets."""
    from apps.assessment.models import TestAttempt
    from apps.experience.models import Experience, ExperienceType
    from apps.idp.models import Task, TaskStatus
    from apps.learning.models import Enrollment, EnrollmentStatus
    from apps.mentorship.models import MentorSession, SessionStatus

    experience = Experience.objects.filter(user=user).aggregate(
        projects=Count("id", filter=Q(type=ExperienceType.PROJECT)),
        competitions=Count(
            "id",
            filter=Q(type__in=[ExperienceType.COMPETITION, ExperienceType.HACKATHON]),
        ),
        volunteer=Count("id", filter=Q(type=ExperienceType.VOLUNTEER)),
    )

    work_months = sum(
        item.duration_months
        for item in Experience.objects.filter(
            user=user,
            type__in=[ExperienceType.WORK, ExperienceType.INTERNSHIP, ExperienceType.FREELANCE],
        )
    )

    return {
        "completed_courses": Enrollment.objects.filter(
            user=user, status=EnrollmentStatus.COMPLETED
        ).count(),
        "passed_tests": TestAttempt.objects.filter(user=user, passed=True).count(),
        "work_experience_months": work_months,
        "verified_skills": UserSkill.objects.filter(
            user=user, status=SkillStatus.VERIFIED
        ).count(),
        "mentor_sessions": MentorSession.objects.filter(
            student=user, status=SessionStatus.COMPLETED
        ).count(),
        "recommendations": 0,  # reserved for the reputation module
        "projects": experience["projects"] or 0,
        "competitions": experience["competitions"] or 0,
        "completed_tasks": Task.objects.filter(user=user, status=TaskStatus.DONE).count(),
        "volunteer_experience": experience["volunteer"] or 0,
    }


def _skill_component(user, dimension) -> tuple[float, int]:
    """Confidence-weighted average proficiency across the axis's skills."""
    links = SkillDimension.objects.filter(dimension=dimension).values_list(
        "skill_id", "weight"
    )
    link_weights = {skill_id: float(weight) for skill_id, weight in links}
    if not link_weights:
        return 0.0, 0

    user_skills = UserSkill.objects.filter(
        user=user, skill_id__in=link_weights.keys()
    ).only("skill_id", "proficiency", "confidence")

    numerator = 0.0
    denominator = 0.0
    counted = 0
    for user_skill in user_skills:
        weight = link_weights[user_skill.skill_id] * float(user_skill.confidence)
        if weight <= 0:
            continue
        numerator += user_skill.proficiency * weight
        denominator += weight
        counted += 1

    return (numerator / denominator if denominator else 0.0), counted


def _activity_component(slug: str, counters: dict[str, int]) -> tuple[float, list[dict]]:
    signals = ACTIVITY_SIGNALS.get(slug, [])
    if not signals:
        return 0.0, []

    total = 0.0
    details = []
    for counter_name, points, cap in signals:
        count = counters.get(counter_name, 0)
        contribution = min(count * points, cap)
        total += contribution
        details.append(
            {"signal": counter_name, "count": count, "points": round(contribution, 1)}
        )
    return min(total, 100.0), details


@transaction.atomic
def recompute_capital_index(user) -> list[CapitalIndex]:
    """Recompute all nine axes for a user."""
    config = CapitalWeightConfig.active()
    weights = config.weights or {}
    skill_weight = float(weights.get("skill_component", 0.7))
    activity_weight = float(weights.get("activity_component", 0.3))

    counters = _collect_activity_counters(user)
    results: list[CapitalIndex] = []

    for dimension in CapitalDimension.objects.all().order_by("order"):
        skill_score, skill_count = _skill_component(user, dimension)
        activity_score, signals = _activity_component(dimension.slug, counters)

        has_data = skill_count > 0 or any(s["count"] for s in signals)

        if skill_count == 0 and signals:
            # No mapped skills: activity carries the axis on its own rather
            # than being diluted by a zero it cannot influence.
            score = activity_score
        elif skill_count > 0 and not signals:
            score = skill_score
        else:
            score = skill_score * skill_weight + activity_score * activity_weight

        index, _created = CapitalIndex.objects.update_or_create(
            user=user,
            dimension=dimension,
            defaults={
                "score": max(0, min(100, round(score))),
                "breakdown": {
                    "has_data": has_data,
                    "skill_score": round(skill_score, 1),
                    "skill_count": skill_count,
                    "activity_score": round(activity_score, 1),
                    "signals": signals,
                    "config_version": config.version,
                },
            },
        )
        results.append(index)

    _maybe_snapshot(user, results)
    return results


def _maybe_snapshot(user, indexes: list[CapitalIndex]) -> None:
    today = timezone.localdate()
    existing = set(
        CapitalSnapshot.objects.filter(user=user, taken_at__date=today).values_list(
            "dimension_id", flat=True
        )
    )
    CapitalSnapshot.objects.bulk_create(
        [
            CapitalSnapshot(
                user=user, dimension_id=index.dimension_id, score=index.score
            )
            for index in indexes
            if index.dimension_id not in existing
        ]
    )


def get_capital_overview(user) -> dict:
    """Radar-chart payload for the personal cabinet (TZ §6)."""
    indexes = list(
        CapitalIndex.objects.filter(user=user)
        .select_related("dimension")
        .order_by("dimension__order")
    )
    if not indexes:
        indexes = recompute_capital_index(user)

    measured = [i for i in indexes if i.breakdown.get("has_data")]
    overall = round(sum(i.score for i in measured) / len(measured)) if measured else 0

    return {
        "overall": overall,
        "measured_axes": len(measured),
        "total_axes": len(indexes),
        "dimensions": [
            {
                "slug": index.dimension.slug,
                "name": index.dimension.name,
                "color": index.dimension.color,
                "icon": index.dimension.icon,
                "score": index.score,
                "has_data": index.breakdown.get("has_data", False),
                "breakdown": index.breakdown,
            }
            for index in indexes
        ],
    }


def get_capital_history(user, days: int = 180) -> list[dict]:
    since = timezone.now() - timezone.timedelta(days=days)
    return [
        {
            "date": snapshot.taken_at.date().isoformat(),
            "dimension": snapshot.dimension.slug,
            "score": snapshot.score,
        }
        for snapshot in CapitalSnapshot.objects.filter(user=user, taken_at__gte=since)
        .select_related("dimension")
        .order_by("taken_at")
    ]
