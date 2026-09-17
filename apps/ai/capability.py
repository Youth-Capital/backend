"""Combined hard + soft skill analysis (TZ §6, §13).

The platform already measures technical ability from tests, courses and work.
The soft-skill assessment adds the other half — how someone works with people,
handles pressure and decides under uncertainty. This module is what reads both
at once and says something an employer could act on.

Three deliberate constraints:

*Different proof, stated as different proof.* A knowledge test observes what
someone can do; a situational-judgement test records what they say they would
do. They are never averaged into one "skill score" — the report keeps them as
two numbers with the gap between them named, because the gap is the finding.

*Codes, not prose.* Every conclusion is a code plus its data, rendered in the
reader's language by the client. The same discipline the matching explanations
follow, and the reason this works identically with the rule-based provider and
with a hosted model behind it.

*Silence over invention.* Fewer than four answered competencies is not a
personality profile. The report says ``insufficient_data`` and stops, rather
than describing a stranger from three answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.assessment.models import AttemptStatus, TestAttempt, TestType
from apps.common.enums import EvidenceSource
from apps.profiles.models import SkillStatus, UserSkill
from apps.taxonomy.services import soft_skill_ids

#: Below this, a competency is worth naming as something to work on.
WEAK_THRESHOLD = 55
#: At or above this, it is worth naming as a strength.
STRONG_THRESHOLD = 70
#: How far hard and soft averages must diverge before the shape is worth
#: reporting as lopsided rather than as noise.
IMBALANCE_POINTS = 20
#: Fewer answered competencies than this and there is no profile to report.
MIN_COMPETENCIES = 4


@dataclass
class CapabilityReport:
    hard: dict = field(default_factory=dict)
    soft: dict = field(default_factory=dict)
    balance: str = "insufficient_data"
    notes: list[dict] = field(default_factory=list)
    next_actions: list[dict] = field(default_factory=list)


def _competency_rows(user, soft_ids: set) -> list[dict]:
    """Soft competencies with the evidence behind each one.

    Read from ``UserSkill`` rather than straight from the last attempt, so a
    competency an employer verified and one the SJT measured land in the same
    list, each carrying which source it came from.
    """
    links = (
        UserSkill.objects.filter(user=user, skill_id__in=soft_ids)
        .select_related("skill", "skill__category")
        .order_by("-proficiency")
    )
    return [
        {
            "skill_id": str(link.skill_id),
            "skill": link.skill.name,
            "category": link.skill.category.name,
            "score": link.proficiency,
            "confidence": float(link.confidence),
            "source": link.best_source,
            # An SJT result is a self-report, so it is shown as measured rather
            # than as verified — the word the UI uses for a graded test.
            "self_reported": link.best_source == EvidenceSource.SOFT_TEST,
            "assessed_at": link.last_evidence_at,
        }
        for link in links
    ]


def _hard_rows(user, soft_ids: set) -> list[dict]:
    links = (
        UserSkill.objects.filter(user=user)
        .exclude(skill_id__in=soft_ids)
        .select_related("skill")
        .order_by("-proficiency")
    )
    return [
        {
            "skill_id": str(link.skill_id),
            "skill": link.skill.name,
            "score": link.proficiency,
            "verified": link.status == SkillStatus.VERIFIED,
            "source": link.best_source,
        }
        for link in links
    ]


def _average(rows: list[dict]) -> int:
    return round(sum(r["score"] for r in rows) / len(rows)) if rows else 0


def build_capability_report(user) -> CapabilityReport:
    soft_ids = soft_skill_ids()
    soft_rows = _competency_rows(user, soft_ids)
    hard_rows = _hard_rows(user, soft_ids)

    last_attempt = (
        TestAttempt.objects.filter(
            user=user,
            test__type=TestType.SOFT_SKILL,
            status__in=[AttemptStatus.GRADED, AttemptStatus.SUBMITTED],
        )
        .select_related("test")
        .order_by("-submitted_at")
        .first()
    )

    hard_avg = _average(hard_rows)
    soft_avg = _average(soft_rows)
    verified = [r for r in hard_rows if r["verified"]]

    report = CapabilityReport(
        hard={
            "average": hard_avg,
            "skill_count": len(hard_rows),
            "verified_count": len(verified),
            "top": hard_rows[:5],
            "weakest": sorted(hard_rows, key=lambda r: r["score"])[:3],
        },
        soft={
            "average": soft_avg,
            "competency_count": len(soft_rows),
            "competencies": soft_rows,
            "strongest": [r for r in soft_rows if r["score"] >= STRONG_THRESHOLD][:3],
            "weakest": [
                r
                for r in sorted(soft_rows, key=lambda r: r["score"])
                if r["score"] < WEAK_THRESHOLD
            ][:3],
            "last_assessment": (
                {
                    "test_id": str(last_attempt.test_id),
                    "title": last_attempt.test.title,
                    "submitted_at": last_attempt.submitted_at,
                    "percentage": last_attempt.percentage,
                }
                if last_attempt
                else None
            ),
        },
    )

    # -- the finding -----------------------------------------------------
    if len(soft_rows) < MIN_COMPETENCIES:
        report.balance = "insufficient_data"
        report.notes.append(
            {
                "code": "capability.take_soft_test",
                "data": {"have": len(soft_rows), "need": MIN_COMPETENCIES},
            }
        )
        report.next_actions.append({"code": "action.take_soft_skill_test"})
        return report

    if not hard_rows:
        report.balance = "soft_only"
        report.notes.append({"code": "capability.no_hard_evidence"})
        report.next_actions.append({"code": "action.take_skill_test"})
        return report

    difference = hard_avg - soft_avg
    if difference >= IMBALANCE_POINTS:
        report.balance = "hard_led"
        report.notes.append(
            {
                "code": "capability.hard_ahead_of_soft",
                "data": {"hard": hard_avg, "soft": soft_avg, "gap": difference},
            }
        )
    elif difference <= -IMBALANCE_POINTS:
        report.balance = "soft_led"
        report.notes.append(
            {
                "code": "capability.soft_ahead_of_hard",
                "data": {"hard": hard_avg, "soft": soft_avg, "gap": -difference},
            }
        )
    else:
        report.balance = "balanced"
        report.notes.append(
            {
                "code": "capability.balanced",
                "data": {"hard": hard_avg, "soft": soft_avg},
            }
        )

    # Named strengths and weak spots, so the report says *which* competency
    # rather than only that the average is low.
    for row in report.soft["strongest"]:
        report.notes.append(
            {
                "code": "capability.soft_strength",
                "data": {"skill": row["skill"], "score": row["score"]},
            }
        )
    for row in report.soft["weakest"]:
        report.notes.append(
            {
                "code": "capability.soft_gap",
                "data": {"skill": row["skill"], "score": row["score"]},
            }
        )
        report.next_actions.append(
            {
                "code": "action.develop_competency",
                "skill_id": row["skill_id"],
                "skill": row["skill"],
                "from": row["score"],
                "to": STRONG_THRESHOLD,
            }
        )

    # Every soft competency being self-reported is worth saying out loud: it is
    # the difference between "measured" and "claimed", and an employer reading
    # this report is entitled to know which one they are looking at.
    if soft_rows and all(r["self_reported"] for r in soft_rows):
        report.notes.append({"code": "capability.soft_all_self_reported"})
        report.next_actions.append({"code": "action.take_soft_skill_test"})

    if not verified:
        report.notes.append({"code": "capability.no_verified_hard_skill"})
        report.next_actions.append({"code": "action.take_skill_test"})

    return report
