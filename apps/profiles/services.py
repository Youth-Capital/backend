"""Profile and skill services."""

from __future__ import annotations

import math
import random
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.common.enums import EVIDENCE_WEIGHTS, EvidenceSource
from apps.common.exceptions import DomainError

from .models import (
    EmployerProfile,
    SkillEvidence,
    SkillStatus,
    StudentProfile,
    UserSkill,
)

#: Evidence from these sources marks a skill as VERIFIED rather than DECLARED.
VERIFYING_SOURCES = frozenset(
    {EvidenceSource.TEST, EvidenceSource.EMPLOYER}
)

#: Older proof still counts, but less. Half-life in days.
EVIDENCE_HALF_LIFE_DAYS = 540


def generate_youth_id() -> str:
    """Human-readable public identifier, e.g. ``YK-2026-4F2A91``.

    Random rather than sequential: a sequential Youth ID would publish how many
    people have registered and let anyone enumerate profiles.
    """
    year = timezone.localdate().year
    for _attempt in range(10):
        suffix = f"{random.getrandbits(24):06X}"
        candidate = f"YK-{year}-{suffix}"
        if not StudentProfile.objects.filter(youth_id=candidate).exists():
            return candidate
    raise DomainError("Could not allocate a Youth ID.", code="youth_id_exhausted")


def generate_employer_slug(name: str) -> str:
    base = slugify(name)[:120] or "company"
    slug, counter = base, 1
    while EmployerProfile.objects.filter(slug=slug).exists():
        counter += 1
        slug = f"{base}-{counter}"[:140]
    return slug


def _decay_factor(issued_at, half_life_days: int = EVIDENCE_HALF_LIFE_DAYS) -> float:
    """Exponential decay so a five-year-old test does not read as current."""
    age_days = max((timezone.now() - issued_at).days, 0)
    return math.pow(0.5, age_days / half_life_days)


@transaction.atomic
def record_skill_evidence(
    *,
    user,
    skill,
    source: str,
    score: int,
    ref_type: str = "",
    ref_id: UUID | None = None,
    issued_by=None,
    note: str = "",
) -> UserSkill:
    """Append evidence for a skill and refresh the aggregate.

    Every module that can say something about a skill — a finished course, a
    graded test, a finished course, an added job — funnels through here, so
    there is exactly one place where proficiency is decided.
    """
    score = max(0, min(100, int(score)))

    user_skill, _created = UserSkill.objects.get_or_create(
        user=user, skill=skill, defaults={"proficiency": 0, "confidence": 0}
    )

    if ref_type and ref_id:
        # Re-running the same course/test must update, not pile up duplicates.
        evidence, created = SkillEvidence.objects.update_or_create(
            user_skill=user_skill,
            source=source,
            ref_type=ref_type,
            ref_id=ref_id,
            defaults={
                "score": score,
                "weight": Decimal(str(EVIDENCE_WEIGHTS.get(source, 0.35))),
                "issued_by": issued_by,
                "issued_at": timezone.now(),
                "note": note[:255],
            },
        )
    else:
        SkillEvidence.objects.update_or_create(
            user_skill=user_skill,
            source=source,
            ref_type="",
            ref_id=None,
            defaults={
                "score": score,
                "weight": Decimal(str(EVIDENCE_WEIGHTS.get(source, 0.35))),
                "issued_by": issued_by,
                "issued_at": timezone.now(),
                "note": note[:255],
            },
        )

    return recalculate_user_skill(user_skill)


def recalculate_user_skill(user_skill: UserSkill) -> UserSkill:
    """Recompute proficiency, confidence and status from the evidence trail."""
    evidence = list(user_skill.evidence.all())
    if not evidence:
        user_skill.proficiency = 0
        user_skill.confidence = Decimal("0")
        user_skill.status = SkillStatus.DECLARED
        user_skill.save(
            update_fields=["proficiency", "confidence", "status", "updated_at"]
        )
        return user_skill

    weighted_sum = 0.0
    weight_total = 0.0
    best_weight = 0.0
    best_source = EvidenceSource.SELF
    verified = False
    latest = None

    for item in evidence:
        decay = _decay_factor(item.issued_at)
        effective = float(item.weight) * decay
        weighted_sum += item.score * effective
        weight_total += effective

        if float(item.weight) > best_weight:
            best_weight = float(item.weight)
            best_source = item.source
        if item.source in VERIFYING_SOURCES:
            verified = True
        if latest is None or item.issued_at > latest:
            latest = item.issued_at

    proficiency = round(weighted_sum / weight_total) if weight_total else 0

    # Confidence is the best surviving evidence weight, not the average: one
    # solid test result should not be dragged down by also having declared it.
    confidence = min(
        1.0, max(float(i.weight) * _decay_factor(i.issued_at) for i in evidence)
    )

    user_skill.proficiency = max(0, min(100, proficiency))
    user_skill.confidence = Decimal(f"{confidence:.2f}")
    user_skill.status = SkillStatus.VERIFIED if verified else SkillStatus.DECLARED
    user_skill.best_source = best_source
    user_skill.last_evidence_at = latest
    user_skill.save(
        update_fields=[
            "proficiency",
            "confidence",
            "status",
            "best_source",
            "last_evidence_at",
            "updated_at",
        ]
    )
    return user_skill


def declare_skill(*, user, skill, proficiency: int) -> UserSkill:
    """User self-reports a skill — the weakest evidence source."""
    return record_skill_evidence(
        user=user,
        skill=skill,
        source=EvidenceSource.SELF,
        score=proficiency,
        note="Self-declared",
    )


def remove_declared_skill(*, user, skill) -> None:
    """Drop a self-declaration.

    Evidence the platform produced (tests, courses) is kept — a user must not
    be able to erase a poor test result by deleting the skill.
    """
    user_skill = UserSkill.objects.filter(user=user, skill=skill).first()
    if user_skill is None:
        return

    user_skill.evidence.filter(source=EvidenceSource.SELF).delete()
    if user_skill.evidence.exists():
        recalculate_user_skill(user_skill)
    else:
        user_skill.delete()


def get_skill_gap(user, profession) -> dict:
    """Compare a user's skills against a profession's requirements.

    Powers the Career Path screen (prompt §5) and the ProfessionMatch score.
    """
    from apps.common.enums import RequirementLevel

    links = list(
        profession.skill_links.select_related("skill", "skill__category").order_by(
            "order"
        )
    )
    owned = {
        us.skill_id: us
        for us in UserSkill.objects.filter(
            user=user, skill_id__in=[link.skill_id for link in links]
        ).select_related("skill")
    }

    matching, partial, missing = [], [], []
    for link in links:
        user_skill = owned.get(link.skill_id)
        entry = {
            "skill_id": str(link.skill_id),
            "skill": link.skill.name,
            "category": link.skill.category.name,
            "requirement": link.requirement,
            "required_level": link.min_proficiency,
            "current_level": user_skill.proficiency if user_skill else 0,
            "verified": bool(user_skill and user_skill.is_verified),
        }
        if user_skill is None or user_skill.proficiency == 0:
            missing.append(entry)
        elif user_skill.proficiency >= link.min_proficiency:
            matching.append(entry)
        else:
            partial.append(entry)

    required = [link for link in links if link.requirement == RequirementLevel.REQUIRED]
    required_met = sum(
        1
        for link in required
        if (owned.get(link.skill_id) and owned[link.skill_id].proficiency >= link.min_proficiency)
    )
    readiness = round(100 * required_met / len(required)) if required else 0

    return {
        "profession_id": str(profession.id),
        "profession": profession.name,
        "readiness": readiness,
        "matching_skills": matching,
        "partial_skills": partial,
        "missing_skills": missing,
        "required_total": len(required),
        "required_met": required_met,
    }


PROFILE_COMPLETION_FIELDS = (
    ("first_name", 10),
    ("last_name", 10),
    ("birth_date", 10),
    ("region_id", 10),
    ("education_status", 10),
    ("target_profession_id", 15),
    ("bio", 5),
    ("avatar", 5),
)


def recalculate_profile_completion(profile: StudentProfile) -> int:
    """Percentage used by the dashboard's "Profile completion" widget."""
    score = 0
    for field, points in PROFILE_COMPLETION_FIELDS:
        value = getattr(profile, field, None)
        if field == "education_status":
            if value and value != "NONE":
                score += points
        elif value:
            score += points

    if UserSkill.objects.filter(user_id=profile.user_id).exists():
        score += 15
    from apps.experience.models import Experience

    if Experience.objects.filter(user_id=profile.user_id).exists():
        score += 10

    score = min(100, score)
    if profile.profile_completion != score:
        profile.profile_completion = score
        profile.save(update_fields=["profile_completion", "updated_at"])
    return score


def touch_activity(user) -> None:
    """Update last_activity_at, throttled to at most once an hour.

    Feeds the manager's Risk list (TZ §14) — "users whose activity dropped".
    Writing on every request would be a needless write per API call.
    """
    profile = getattr(user, "student_profile", None)
    if profile is None:
        return
    if timezone.now() - profile.last_activity_at > timedelta(hours=1):
        profile.last_activity_at = timezone.now()
        profile.save(update_fields=["last_activity_at"])


def can_view_student_profile(*, viewer, student_user) -> bool:
    """Whether `viewer` may see a student's identified profile.

    Employers get pseudonymised cards in talent search unless the student
    applied to them, made the profile public, or consented to talent search
    (docs/02-ARCHITECTURE.md §5).
    """
    from apps.accounts.services import has_consent
    from apps.accounts.models import ConsentType

    if viewer.id == student_user.id or viewer.is_admin:
        return True

    if viewer.is_employer:
        from apps.jobs.models import Application

        company = getattr(viewer, "employer_profile", None)
        if company and Application.objects.filter(
            student_id=student_user.id, vacancy__employer=company
        ).exists():
            return True
        if has_consent(student_user, ConsentType.TALENT_SEARCH):
            return True

    public = getattr(student_user, "public_profile", None)
    return bool(public and public.is_public)


def search_skills(query: str, limit: int = 20):
    """Skill lookup that also matches the alias list."""
    from apps.taxonomy.models import Skill

    query = (query or "").strip()
    if not query:
        return Skill.objects.none()
    return (
        Skill.objects.filter(is_active=True)
        .filter(
            Q(name_uz__icontains=query)
            | Q(name_ru__icontains=query)
            | Q(name_en__icontains=query)
            | Q(aliases__icontains=query)
        )
        .select_related("category")[:limit]
    )
