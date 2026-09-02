"""Knowledge score computation."""

from __future__ import annotations

import math
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.common.enums import EvidenceSource
from apps.profiles.models import SkillEvidence, UserSkill

from .models import KnowledgeConfig, KnowledgeScore, KnowledgeSnapshot

#: Self-declaration is not an assessment — see the module docstring in models.py.
OBJECTIVE_SOURCES = frozenset(
    {
        EvidenceSource.TEST,
        EvidenceSource.COURSE,
        EvidenceSource.EXPERIENCE,
        EvidenceSource.MENTOR,
        EvidenceSource.EMPLOYER,
    }
)


def _decay(issued_at, half_life_days: int) -> float:
    age_days = max((timezone.now() - issued_at).days, 0)
    return math.pow(0.5, age_days / half_life_days)


@transaction.atomic
def recompute_knowledge_for_skill(user, skill) -> KnowledgeScore | None:
    """Recompute one user's knowledge score for one skill.

    Returns None (and deletes any stale row) when no objective evidence exists —
    an empty radar chart is more honest than one built from self-reports.
    """
    config = KnowledgeConfig.active()
    weights = config.weights or {}

    user_skill = UserSkill.objects.filter(user=user, skill=skill).first()
    if user_skill is None:
        KnowledgeScore.objects.filter(user=user, skill=skill).delete()
        return None

    evidence = [
        item
        for item in user_skill.evidence.all()
        if item.source in OBJECTIVE_SOURCES
    ]
    if not evidence:
        KnowledgeScore.objects.filter(user=user, skill=skill).delete()
        return None

    weighted_sum = 0.0
    weight_total = 0.0
    per_source: dict[str, list[float]] = {}

    for item in evidence:
        source_weight = float(weights.get(item.source, 0.1))
        decay = _decay(item.issued_at, config.decay_half_life_days)
        effective = source_weight * decay
        weighted_sum += item.score * effective
        weight_total += effective
        per_source.setdefault(item.source, []).append(float(item.score))

    score = round(weighted_sum / weight_total) if weight_total else 0

    # Confidence rises with corroboration: one test is decent, three
    # independent sources agreeing is stronger.
    distinct_sources = len(per_source)
    confidence = min(1.0, 0.45 + 0.18 * distinct_sources + 0.02 * len(evidence))

    knowledge, _created = KnowledgeScore.objects.update_or_create(
        user=user,
        skill=skill,
        defaults={
            "score": max(0, min(100, score)),
            "confidence": Decimal(f"{confidence:.2f}"),
            "evidence_count": len(evidence),
            "breakdown": {
                "by_source": {
                    source: round(sum(values) / len(values), 1)
                    for source, values in per_source.items()
                },
                "weights": {k: v for k, v in weights.items() if k in per_source},
                "config_version": config.version,
                "half_life_days": config.decay_half_life_days,
            },
        },
    )
    _maybe_snapshot(knowledge)
    return knowledge


def _maybe_snapshot(knowledge: KnowledgeScore) -> None:
    """Record history, but at most one row per skill per day."""
    today = timezone.localdate()
    exists = KnowledgeSnapshot.objects.filter(
        user_id=knowledge.user_id, skill_id=knowledge.skill_id, taken_at__date=today
    ).exists()
    if not exists:
        KnowledgeSnapshot.objects.create(
            user_id=knowledge.user_id,
            skill_id=knowledge.skill_id,
            score=knowledge.score,
        )


def recompute_all_knowledge(user) -> int:
    """Recompute every knowledge score for a user. Returns rows touched."""
    skill_ids = (
        SkillEvidence.objects.filter(
            user_skill__user=user, source__in=OBJECTIVE_SOURCES
        )
        .values_list("user_skill__skill_id", flat=True)
        .distinct()
    )
    from apps.taxonomy.models import Skill

    count = 0
    for skill in Skill.objects.filter(id__in=list(skill_ids)):
        if recompute_knowledge_for_skill(user, skill) is not None:
            count += 1
    return count


def get_knowledge_overview(user, limit: int = 12) -> dict:
    """Data behind the Knowledge Analytics panel (prompt §4)."""
    scores = list(
        KnowledgeScore.objects.filter(user=user)
        .select_related("skill", "skill__category")
        .order_by("-score")[:limit]
    )

    average = round(sum(s.score for s in scores) / len(scores)) if scores else 0
    by_category: dict[str, list[int]] = {}
    for item in scores:
        by_category.setdefault(item.skill.category.name, []).append(item.score)

    return {
        "average_score": average,
        "tracked_skills": len(scores),
        "topics": [
            {
                "skill_id": str(s.skill_id),
                "skill": s.skill.name,
                "category": s.skill.category.name,
                "score": s.score,
                "band": s.band,
                "confidence": float(s.confidence),
                "evidence_count": s.evidence_count,
            }
            for s in scores
        ],
        "by_category": [
            {"category": name, "score": round(sum(v) / len(v))}
            for name, v in sorted(by_category.items())
        ],
    }


def get_knowledge_history(user, skill=None, days: int = 180) -> list[dict]:
    since = timezone.now() - timezone.timedelta(days=days)
    queryset = KnowledgeSnapshot.objects.filter(user=user, taken_at__gte=since)
    if skill is not None:
        queryset = queryset.filter(skill=skill)
    return [
        {
            "date": snapshot.taken_at.date().isoformat(),
            "skill": snapshot.skill.name,
            "skill_id": str(snapshot.skill_id),
            "score": snapshot.score,
        }
        for snapshot in queryset.select_related("skill").order_by("taken_at")
    ]
