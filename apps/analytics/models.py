"""Event taxonomy and pre-aggregated metrics (TZ §15, §18, epic E13)."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class EventName(models.TextChoices):
    """The analytics event taxonomy from TZ §18.

    Fixed vocabulary rather than free-form strings — a funnel built on typo'd
    event names silently under-reports and nobody notices for a quarter.
    """

    SIGNED_UP = "signed_up", "Signed up"
    ONBOARDING_COMPLETED = "onboarding_completed", "Onboarding completed"
    DIAGNOSTIC_COMPLETED = "diagnostic_completed", "Diagnostic completed"
    PROFESSION_SELECTED = "profession_selected", "Profession selected"
    PLAN_GENERATED = "plan_generated", "Development plan generated"
    PLAN_ACTIVATED = "plan_activated", "Development plan activated"
    TASK_COMPLETED = "task_completed", "Task completed"
    COURSE_ENROLLED = "course_enrolled", "Course enrolled"
    LESSON_COMPLETED = "lesson_completed", "Lesson completed"
    COURSE_COMPLETED = "course_completed", "Course completed"
    TEST_STARTED = "test_started", "Test started"
    TEST_SUBMITTED = "test_submitted", "Test submitted"
    SKILL_VERIFIED = "skill_verified", "Skill verified"
    EXPERIENCE_ADDED = "experience_added", "Experience added"
    CV_CREATED = "cv_created", "CV created"
    VACANCY_VIEWED = "vacancy_viewed", "Vacancy viewed"
    APPLICATION_SUBMITTED = "application_submitted", "Application submitted"
    APPLICATION_STATUS_CHANGED = "application_status_changed", "Application status changed"
    INTERVIEW_SCHEDULED = "interview_scheduled", "Interview scheduled"
    HIRED = "hired", "Hired"
    MENTOR_SESSION_REQUESTED = "mentor_session_requested", "Mentor session requested"
    MENTOR_SESSION_COMPLETED = "mentor_session_completed", "Mentor session completed"
    RECOMMENDATION_ACCEPTED = "recommendation_accepted", "Recommendation accepted"
    RECOMMENDATION_DISMISSED = "recommendation_dismissed", "Recommendation dismissed"


class AnalyticsEvent(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_events",
    )
    session_id = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=48, choices=EventName.choices)
    properties = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    #: Hashed, not stored raw — an IP is personal data and we only need it for
    #: coarse deduplication.
    ip_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "analytics_event"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["name", "-occurred_at"]),
            models.Index(fields=["user", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} @ {self.occurred_at:%Y-%m-%d %H:%M}"


class DailyMetric(BaseModel):
    """Pre-aggregated counters for the dashboards.

    The manager panel (TZ §14) slices by region, age band and education status;
    running those aggregations live over the event table would get slower every
    week of the programme.
    """

    date = models.DateField(db_index=True)
    key = models.CharField(max_length=64)
    dimension_key = models.CharField(max_length=32, blank=True)
    dimension_value = models.CharField(max_length=64, blank=True)
    value = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = "analytics_daily_metric"
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "key", "dimension_key", "dimension_value"],
                name="uniq_daily_metric",
            )
        ]
        indexes = [models.Index(fields=["key", "-date"])]

    def __str__(self) -> str:
        return f"{self.date} {self.key}={self.value}"
