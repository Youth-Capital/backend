"""CV quality rating.

A number an employer can act on, and a number the student can raise on purpose.

Two rules shape the formula.

*It scores the evidence, not the prose.* A CV with a beautifully written
summary and nothing behind it must not outrank one with four tested skills and
a job. So the heaviest components read the same trail matching reads —
``SkillEvidence`` — rather than counting characters.

*Every point is attributable.* The breakdown says which component gave which
points and what is missing from each, because "your CV scores 61" is not
feedback and an employer comparing two candidates deserves to see what the
difference is made of.

The score is stored on the document (``CVDocument.quality_score``) so a
candidate list can rank fifty people without recomputing fifty times; it is
recomputed whenever the CV is opened, edited, or a candidate card is built.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from django.utils import timezone

from apps.common.enums import EvidenceSource
from apps.experience.models import Experience
from apps.learning.models import Certificate, Enrollment, EnrollmentStatus
from apps.profiles.models import Education, SkillStatus, UserSkill

from .models import CVDocument, PortfolioItem

#: Sources that mean somebody other than the author vouched for the skill.
PROVEN_SOURCES = frozenset(
    {EvidenceSource.TEST, EvidenceSource.EMPLOYER}
)

#: Component weights. They sum to 100 — asserted in the tests, because a
#: formula that silently stops adding up to 100 makes every stored score wrong.
WEIGHTS: dict[str, int] = {
    "completeness": 20,
    "skills": 20,
    "proof": 25,
    "experience": 15,
    "portfolio": 10,
    "targeting": 10,
}


@dataclass
class Component:
    key: str
    score: int
    max: int
    #: Fact codes the UI renders in the reader's language, never English prose.
    facts: list[dict] = field(default_factory=list)
    tips: list[dict] = field(default_factory=list)


def _band(score: int) -> str:
    if score >= 85:
        return "EXCELLENT"
    if score >= 70:
        return "STRONG"
    if score >= 50:
        return "FAIR"
    if score >= 30:
        return "WEAK"
    return "EMPTY"


def _cap(value: float, ceiling: int) -> int:
    return max(0, min(ceiling, round(value)))


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def _completeness(cv: CVDocument, profile) -> Component:
    """Is the document actually filled in?

    Cheap points on purpose: this is the part a student can fix in ten minutes,
    and a CV that scores zero here is not ready to be sent anywhere.
    """
    ceiling = WEIGHTS["completeness"]
    points = 0
    tips: list[dict] = []

    if cv.headline.strip():
        points += 3
    else:
        tips.append({"code": "cv.add_headline", "severity": "medium", "gain": 3})

    summary = (cv.summary or "").strip()
    if len(summary) >= 200:
        points += 5
    elif len(summary) >= 60:
        points += 3
        tips.append({"code": "cv.expand_summary", "severity": "low", "gain": 2})
    else:
        tips.append({"code": "cv.add_summary", "severity": "high", "gain": 5})

    if Education.objects.filter(user=cv.user).exists():
        points += 4
    else:
        tips.append({"code": "cv.add_education", "severity": "medium", "gain": 4})

    if profile is not None and profile.avatar:
        points += 2
    else:
        tips.append({"code": "cv.add_avatar", "severity": "low", "gain": 2})

    if profile is not None and (profile.languages or []):
        points += 3
    else:
        tips.append({"code": "cv.add_languages", "severity": "medium", "gain": 3})

    if profile is not None and (profile.region_id or profile.city):
        points += 3
    else:
        tips.append({"code": "cv.add_location", "severity": "low", "gain": 3})

    return Component(
        key="completeness",
        score=_cap(points, ceiling),
        max=ceiling,
        facts=[{"code": "cv.fact.summary_length", "data": {"chars": len(summary)}}],
        tips=tips,
    )


def _skills(skills: list[UserSkill]) -> Component:
    """Breadth. Ten skills is a profile; two is a placeholder.

    Deliberately saturating: the twentieth skill adds nothing, because a wall
    of self-declared skills is what this formula must not reward.
    """
    ceiling = WEIGHTS["skills"]
    count = len(skills)
    strong = [s for s in skills if s.proficiency >= 50]

    # 8 skills reaches the breadth ceiling; strength is the other half.
    breadth = min(count, 8) / 8 * (ceiling * 0.6)
    depth = min(len(strong), 5) / 5 * (ceiling * 0.4)

    tips: list[dict] = []
    if count < 5:
        tips.append(
            {"code": "cv.add_skills", "severity": "high", "data": {"have": count, "want": 8}}
        )
    if not strong:
        tips.append({"code": "cv.raise_proficiency", "severity": "medium"})

    return Component(
        key="skills",
        score=_cap(breadth + depth, ceiling),
        max=ceiling,
        facts=[
            {"code": "cv.fact.skill_count", "data": {"count": count}},
            {"code": "cv.fact.strong_skills", "data": {"count": len(strong)}},
        ],
        tips=tips,
    )


def _proof(cv: CVDocument, skills: list[UserSkill]) -> Component:
    """The heaviest component, and the one that cannot be typed in.

    A skill counts here only when a test or an employer put it there.
    This is the difference between a CV and a claim.
    """
    ceiling = WEIGHTS["proof"]
    verified = [s for s in skills if s.status == SkillStatus.VERIFIED]
    proven = [s for s in skills if s.best_source in PROVEN_SOURCES]

    certificates = Certificate.objects.filter(user=cv.user).count()
    completed_courses = Enrollment.objects.filter(
        user=cv.user, status=EnrollmentStatus.COMPLETED
    ).count()

    # Share of the profile that is proven, not the raw count: five verified
    # skills out of five says more than five out of forty.
    share = len(proven) / len(skills) if skills else 0
    points = share * (ceiling * 0.5)
    points += min(len(verified), 5) / 5 * (ceiling * 0.25)
    points += min(certificates, 3) / 3 * (ceiling * 0.15)
    points += min(completed_courses, 3) / 3 * (ceiling * 0.10)

    tips: list[dict] = []
    if not proven:
        tips.append({"code": "cv.verify_skills", "severity": "high"})
    elif share < 0.5:
        tips.append(
            {
                "code": "cv.verify_more_skills",
                "severity": "medium",
                "data": {"proven": len(proven), "total": len(skills)},
            }
        )
    if not certificates:
        tips.append({"code": "cv.earn_certificate", "severity": "medium"})

    return Component(
        key="proof",
        score=_cap(points, ceiling),
        max=ceiling,
        facts=[
            {"code": "cv.fact.verified_skills", "data": {"count": len(verified)}},
            {
                "code": "cv.fact.proven_share",
                "data": {"percent": round(share * 100), "total": len(skills)},
            },
            {"code": "cv.fact.certificates", "data": {"count": certificates}},
        ],
        tips=tips,
    )


def _experience(cv: CVDocument) -> Component:
    ceiling = WEIGHTS["experience"]
    entries = list(
        Experience.objects.filter(user=cv.user).prefetch_related("skill_links")
    )
    months = sum(e.duration_months or 0 for e in entries)
    tagged = [e for e in entries if e.skill_links.exists()]
    verified = [e for e in entries if e.verification_status == "VERIFIED"]

    points = min(len(entries), 3) / 3 * (ceiling * 0.4)
    points += min(months, 24) / 24 * (ceiling * 0.3)
    # Untagged experience is invisible to matching — it is prose, not data.
    points += (len(tagged) / len(entries) if entries else 0) * (ceiling * 0.2)
    points += min(len(verified), 2) / 2 * (ceiling * 0.1)

    tips: list[dict] = []
    if not entries:
        tips.append({"code": "cv.add_experience", "severity": "high"})
    elif len(tagged) < len(entries):
        tips.append(
            {
                "code": "cv.tag_experience_skills",
                "severity": "medium",
                "data": {"count": len(entries) - len(tagged)},
            }
        )

    return Component(
        key="experience",
        score=_cap(points, ceiling),
        max=ceiling,
        facts=[
            {"code": "cv.fact.experience_entries", "data": {"count": len(entries)}},
            {"code": "cv.fact.experience_months", "data": {"months": months}},
        ],
        tips=tips,
    )


def _portfolio(cv: CVDocument) -> Component:
    ceiling = WEIGHTS["portfolio"]
    items = list(
        PortfolioItem.objects.filter(user=cv.user, is_public=True).prefetch_related(
            "skills"
        )
    )
    # An item with neither a link nor a description is a title — not evidence.
    substantial = [i for i in items if (i.url or i.file) and i.description.strip()]

    points = min(len(items), 3) / 3 * (ceiling * 0.6)
    points += min(len(substantial), 2) / 2 * (ceiling * 0.4)

    tips: list[dict] = []
    if not items:
        tips.append({"code": "cv.add_portfolio", "severity": "medium"})
    elif not substantial:
        tips.append({"code": "cv.describe_portfolio", "severity": "low"})

    return Component(
        key="portfolio",
        score=_cap(points, ceiling),
        max=ceiling,
        facts=[{"code": "cv.fact.portfolio_items", "data": {"count": len(items)}}],
        tips=tips,
    )


def _targeting(cv: CVDocument, profile, skills: list[UserSkill]) -> Component:
    """Is this CV aimed at a role, or sent at everything?

    Readiness against the target is read from the profession match the platform
    already computes, so this component agrees with the Career page rather than
    inventing a second opinion.
    """
    ceiling = WEIGHTS["targeting"]
    target = cv.target_profession or getattr(profile, "target_profession", None)

    tips: list[dict] = []
    if target is None:
        return Component(
            key="targeting",
            score=0,
            max=ceiling,
            tips=[{"code": "cv.set_target_profession", "severity": "high"}],
        )

    points = ceiling * 0.4  # having a target at all

    from apps.matching.models import ProfessionMatch

    match = ProfessionMatch.objects.filter(
        student=cv.user, profession=target
    ).first()
    readiness = match.score if match else 0
    points += readiness / 100 * (ceiling * 0.6)

    if readiness < 60:
        tips.append(
            {
                "code": "cv.close_target_gap",
                "severity": "medium",
                "data": {"profession": target.name, "readiness": readiness},
            }
        )
    if cv.target_profession_id is None:
        tips.append({"code": "cv.aim_cv_at_target", "severity": "low"})

    return Component(
        key="targeting",
        score=_cap(points, ceiling),
        max=ceiling,
        facts=[
            {
                "code": "cv.fact.target",
                "data": {"profession": target.name, "readiness": readiness},
            }
        ],
        tips=tips,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_cv(cv: CVDocument) -> dict:
    """Compute the rating without persisting it."""
    profile = getattr(cv.user, "student_profile", None)
    skills = list(
        UserSkill.objects.filter(user=cv.user).select_related("skill")
    )

    components = [
        _completeness(cv, profile),
        _skills(skills),
        _proof(cv, skills),
        _experience(cv),
        _portfolio(cv),
        _targeting(cv, profile, skills),
    ]

    overall = sum(c.score for c in components)
    tips = [tip for c in components for tip in c.tips]
    # Highest-severity first, so the one thing worth doing next is at the top.
    order = {"high": 0, "medium": 1, "low": 2}
    tips.sort(key=lambda t: order.get(t.get("severity", "low"), 3))

    return {
        "overall": overall,
        "band": _band(overall),
        "components": [asdict(c) for c in components],
        "tips": tips[:6],
        "computed_at": timezone.now().isoformat(),
    }


def refresh_cv_rating(cv: CVDocument) -> dict:
    """Compute and store. Returns the same payload as :func:`score_cv`."""
    result = score_cv(cv)
    CVDocument.objects.filter(pk=cv.pk).update(
        quality_score=result["overall"],
        quality_breakdown=result,
        quality_computed_at=timezone.now(),
    )
    cv.quality_score = result["overall"]
    cv.quality_breakdown = result
    return result


def rating_for_employer(user) -> dict | None:
    """The rating an employer sees for a candidate: their primary CV.

    Recomputed rather than read from the column, because a stale number on a
    hiring screen is worse than a slightly slower one, and this runs for a
    single candidate at a time.
    """
    cv = (
        CVDocument.objects.filter(user=user)
        .select_related("target_profession")
        .order_by("-is_primary", "-updated_at")
        .first()
    )
    if cv is None:
        return None
    result = refresh_cv_rating(cv)
    return {
        "cv_id": str(cv.id),
        "title": cv.title,
        "template": cv.template,
        "language": cv.language,
        "updated_at": cv.updated_at,
        "overall": result["overall"],
        "band": result["band"],
        "components": result["components"],
    }
