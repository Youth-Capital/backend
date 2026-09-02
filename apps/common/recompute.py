"""The recomputation chain (prompt §32).

The prompt's requirement is that finishing a lesson ripples all the way to a
vacancy match score. That ripple lives here, in one idempotent entry point,
rather than being re-implemented in every view that happens to change a skill.

    lesson done -> enrollment progress -> course completed -> skill evidence
        -> UserSkill -> KnowledgeScore -> CapitalIndex
        -> ProfessionMatch -> MatchResult marked stale -> plan progress

Match results are *marked stale* rather than recomputed inline: a single
completed course can touch hundreds of vacancy pairs, and a user should not
wait for that inside an HTTP request.
"""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def recompute_for_user(user, *, skills=None, reason: str = "") -> dict:
    """Bring every derived value for `user` back in sync.

    `skills` narrows the knowledge recomputation to the skills that actually
    changed; omit it to rebuild everything (used by the seeder and by repair
    commands).

    Safe to call repeatedly — every step is an upsert.
    """
    from apps.capital.services import recompute_capital_index
    from apps.knowledge.services import recompute_all_knowledge, recompute_knowledge_for_skill
    from apps.matching.services import mark_matches_stale, recompute_profession_matches
    from apps.profiles.services import recalculate_profile_completion

    summary = {"reason": reason, "knowledge": 0, "capital": 0, "matches_stale": 0}

    try:
        if skills:
            for skill in skills:
                recompute_knowledge_for_skill(user, skill)
            summary["knowledge"] = len(skills)
        else:
            summary["knowledge"] = recompute_all_knowledge(user)
    except Exception:
        logger.exception("Knowledge recomputation failed for user=%s", user.id)

    try:
        summary["capital"] = len(recompute_capital_index(user))
    except Exception:
        logger.exception("Capital recomputation failed for user=%s", user.id)

    try:
        recompute_profession_matches(user)
    except Exception:
        logger.exception("Profession match recomputation failed for user=%s", user.id)

    try:
        summary["matches_stale"] = mark_matches_stale(student=user)
    except Exception:
        logger.exception("Marking matches stale failed for user=%s", user.id)

    profile = getattr(user, "student_profile", None)
    if profile is not None:
        try:
            recalculate_profile_completion(profile)
        except Exception:
            logger.exception("Profile completion recalculation failed for user=%s", user.id)

    try:
        _refresh_plan_progress(user)
    except Exception:
        logger.exception("Plan progress recalculation failed for user=%s", user.id)

    logger.info("Recomputed derived data for user=%s (%s): %s", user.id, reason, summary)
    return summary


def _refresh_plan_progress(user) -> None:
    from apps.idp.services import recalculate_plan_progress
    from apps.idp.models import DevelopmentPlan, PlanStatus

    for plan in DevelopmentPlan.objects.filter(user=user, status=PlanStatus.ACTIVE):
        recalculate_plan_progress(plan)


def schedule_recompute(user, *, skills=None, reason: str = "") -> None:
    """Queue the recomputation, or run it inline when no broker is configured.

    Keeping the decision in one place means domain code never has to care
    whether Celery is available in this deployment.
    """
    if settings.RECOMPUTE_SYNCHRONOUS:
        recompute_for_user(user, skills=skills, reason=reason)
        return

    try:
        from apps.common.tasks import recompute_for_user_task

        recompute_for_user_task.delay(
            str(user.id),
            [str(s.id) for s in skills] if skills else None,
            reason,
        )
    except Exception:  # pragma: no cover - broker down should not lose the work
        logger.exception("Falling back to synchronous recompute for user=%s", user.id)
        recompute_for_user(user, skills=skills, reason=reason)
