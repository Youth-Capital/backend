"""Daily activity streaks.

Why this exists: nothing in the product gave a reason to come back tomorrow.
Fourteen notification types all reacted to something that had already
happened — a course finished, a test graded — and none of them created the
small daily pull that turns intent into a habit.

The measured effect is large. Published analyses of Duolingo put streak-driven
retention at roughly 12% → 55%, and a ten-day streak is the point where
drop-off falls sharply. This platform's cycle is longer than a five-minute
language lesson, so the effect will be smaller — but the direction is
consistent across every study of learner retention.

Deliberately restrained. Research on gamification also shows it backfires when
"the game mechanics are louder than the learning": no points, no leagues, no
badges. A streak counts days of real work and nothing else.
"""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.common.models import BaseModel

#: A day counts once at least this many meaningful actions are done. One is
#: deliberate: the bar for "I showed up" has to be reachable on a bad day, or
#: the streak becomes a source of guilt rather than momentum.
DEFAULT_DAILY_GOAL = 1

#: Missing a single day does not reset a long streak. People have exams,
#: illness and no internet; a rule that punishes one bad day teaches them the
#: streak is not worth starting again.
GRACE_DAYS = 1


class LearningStreak(BaseModel):
    """One row per learner, holding their run of active days."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="streak"
    )

    current_days = models.PositiveIntegerField(default=0)
    longest_days = models.PositiveIntegerField(default=0)

    #: The last day that met the goal. Null until the first one does.
    last_active_on = models.DateField(null=True, blank=True, db_index=True)
    #: Actions recorded today; reset when the date rolls over.
    actions_today = models.PositiveSmallIntegerField(default=0)
    counted_on = models.DateField(null=True, blank=True)

    daily_goal = models.PositiveSmallIntegerField(default=DEFAULT_DAILY_GOAL)
    total_active_days = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "idp_learning_streak"

    def __str__(self) -> str:
        return f"{self.user_id}: {self.current_days}d"

    @property
    def is_alive(self) -> bool:
        """Is the streak still running, without needing a nightly job?

        Computed on read. A streak that only dies when a scheduler happens to
        run would show a stale number to whoever looks first.
        """
        if not self.last_active_on:
            return False
        return (timezone.localdate() - self.last_active_on).days <= GRACE_DAYS + 1

    @property
    def done_today(self) -> bool:
        return (
            self.counted_on == timezone.localdate()
            and self.actions_today >= self.daily_goal
        )

    @property
    def at_risk(self) -> bool:
        """A live streak that today has not yet secured."""
        return self.is_alive and self.current_days > 0 and not self.done_today


@transaction.atomic
def record_activity(user, *, on: date | None = None) -> LearningStreak | None:
    """Count one meaningful action toward today's goal.

    Called from the places where real work lands — a lesson completed, a plan
    task closed, a test submitted. Not from page views: a streak has to mean
    effort, or it means nothing.
    """
    if not getattr(user, "is_student", False):
        return None

    today = on or timezone.localdate()
    streak, _ = LearningStreak.objects.select_for_update().get_or_create(user=user)

    if streak.counted_on != today:
        streak.counted_on = today
        streak.actions_today = 0

    streak.actions_today += 1

    if streak.actions_today >= streak.daily_goal and streak.last_active_on != today:
        gap = (today - streak.last_active_on).days if streak.last_active_on else None

        if gap is None or gap > GRACE_DAYS + 1:
            streak.current_days = 1          # first day, or too long a gap
        elif gap == 0:
            pass                             # already counted today
        else:
            streak.current_days += 1         # yesterday, or one forgiven day

        streak.last_active_on = today
        streak.total_active_days += 1
        streak.longest_days = max(streak.longest_days, streak.current_days)

    streak.save()
    return streak


def overview(user) -> dict:
    """What the dashboard shows. Safe for a learner who has never been active."""
    streak = LearningStreak.objects.filter(user=user).first()
    if streak is None:
        return {
            "current_days": 0,
            "longest_days": 0,
            "done_today": False,
            "at_risk": False,
            "daily_goal": DEFAULT_DAILY_GOAL,
            "actions_today": 0,
            "total_active_days": 0,
        }

    alive = streak.is_alive
    return {
        "current_days": streak.current_days if alive else 0,
        "longest_days": streak.longest_days,
        "done_today": streak.done_today,
        "at_risk": streak.at_risk,
        "daily_goal": streak.daily_goal,
        "actions_today": streak.actions_today
        if streak.counted_on == timezone.localdate()
        else 0,
        "total_active_days": streak.total_active_days,
    }
