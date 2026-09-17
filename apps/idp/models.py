"""Individual Development Plan — the core of the personal cabinet (TZ §6, §19).

This is the module the prompt omits and the TZ treats as P0: goals, a 90-day
plan, milestones, dated tasks, and "the three things to do today" from the
first-demo script (TZ §22.1).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.enums import Priority
from apps.common.models import BaseModel
from apps.taxonomy.models import Profession, Skill


class GoalHorizon(models.TextChoices):
    M3 = "M3", _("3 months")
    Y1 = "Y1", _("1 year")
    Y3 = "Y3", _("3 years")


class GoalStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    ACHIEVED = "ACHIEVED", _("Achieved")
    DROPPED = "DROPPED", _("Dropped")


class Origin(models.TextChoices):
    USER = "USER", _("User")
    AI = "AI", _("AI generated")


class Goal(BaseModel):
    """TZ §6: goals at 3-month, 1-year and 3-year horizons."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="goals"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, max_length=2000)
    horizon = models.CharField(
        max_length=2, choices=GoalHorizon.choices, default=GoalHorizon.M3
    )
    target_profession = models.ForeignKey(
        Profession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="goals",
    )
    target_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=8, choices=GoalStatus.choices, default=GoalStatus.ACTIVE
    )
    progress = models.PositiveSmallIntegerField(default=0)
    source = models.CharField(
        max_length=8, choices=Origin.choices, default=Origin.USER
    )

    class Meta:
        db_table = "idp_goal"
        ordering = ["horizon", "-created_at"]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self) -> str:
        return self.title


class PlanStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    ACTIVE = "ACTIVE", _("Active")
    COMPLETED = "COMPLETED", _("Completed")
    ARCHIVED = "ARCHIVED", _("Archived")


class DevelopmentPlan(BaseModel):
    """The 90-day plan required by TZ acceptance criterion §19 "IDP"."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plans"
    )
    goal = models.ForeignKey(
        Goal, on_delete=models.SET_NULL, null=True, blank=True, related_name="plans"
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, max_length=2000)

    period_days = models.PositiveSmallIntegerField(default=90)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=10, choices=PlanStatus.choices, default=PlanStatus.DRAFT
    )
    source = models.CharField(max_length=8, choices=Origin.choices, default=Origin.AI)
    ai_request = models.ForeignKey(
        "ai.AIRequestLog",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="plans",
    )
    progress = models.PositiveSmallIntegerField(default=0)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_plans",
    )

    class Meta:
        db_table = "idp_development_plan"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="ACTIVE"),
                name="one_active_plan_per_user",
            )
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def days_remaining(self) -> int:
        if not self.end_date:
            return 0
        return max((self.end_date - timezone.localdate()).days, 0)


class MilestoneStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    IN_PROGRESS = "IN_PROGRESS", _("In progress")
    DONE = "DONE", _("Done")
    MISSED = "MISSED", _("Missed")


class Milestone(BaseModel):
    plan = models.ForeignKey(
        DevelopmentPlan, on_delete=models.CASCADE, related_name="milestones"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, max_length=2000)
    due_date = models.DateField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=12, choices=MilestoneStatus.choices, default=MilestoneStatus.PENDING
    )
    progress = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "idp_milestone"
        ordering = ["order", "due_date"]

    def __str__(self) -> str:
        return self.title


class TaskType(models.TextChoices):
    COURSE = "COURSE", _("Take a course")
    LESSON = "LESSON", _("Complete a lesson")
    TEST = "TEST", _("Pass a test")
    PROJECT = "PROJECT", _("Build a project")
    APPLICATION = "APPLICATION", _("Apply to a vacancy")
    PROFILE = "PROFILE", _("Complete your profile")
    CUSTOM = "CUSTOM", _("Custom")


class TaskStatus(models.TextChoices):
    TODO = "TODO", _("To do")
    IN_PROGRESS = "IN_PROGRESS", _("In progress")
    DONE = "DONE", _("Done")
    SKIPPED = "SKIPPED", _("Skipped")


class Task(BaseModel):
    """A single concrete next action.

    `ref_type` / `ref_id` are a soft reference rather than a foreign key: a task
    can point at a course, a test or a vacancy, and hard-linking all three would
    mean three nullable FKs and a check constraint for no real benefit.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tasks"
    )
    plan = models.ForeignKey(
        DevelopmentPlan,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tasks",
    )
    milestone = models.ForeignKey(
        Milestone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, max_length=2000)
    type = models.CharField(
        max_length=16, choices=TaskType.choices, default=TaskType.CUSTOM
    )
    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.UUIDField(null=True, blank=True)

    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.MEDIUM
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=TaskStatus.choices, default=TaskStatus.TODO
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    estimated_minutes = models.PositiveSmallIntegerField(default=30)
    order = models.PositiveSmallIntegerField(default=0)

    related_skills = models.ManyToManyField(Skill, blank=True, related_name="tasks")
    source = models.CharField(max_length=8, choices=Origin.choices, default=Origin.AI)

    class Meta:
        db_table = "idp_task"
        ordering = ["due_date", "order", "-priority"]
        indexes = [
            # Backs the "3 tasks for today" widget without a table scan.
            models.Index(fields=["user", "status", "due_date"]),
            models.Index(fields=["plan", "status"]),
            models.Index(fields=["ref_type", "ref_id"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_overdue(self) -> bool:
        return (
            self.status in {TaskStatus.TODO, TaskStatus.IN_PROGRESS}
            and self.due_date is not None
            and self.due_date < timezone.localdate()
        )


# Habit tracking lives in its own module for readability; re-exported so every
# idp model is importable from one place.
from .streaks import LearningStreak  # noqa: E402

__all__ = ["LearningStreak"]
