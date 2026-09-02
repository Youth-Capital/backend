"""Mentorship (TZ §8.6, epic E10)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.profiles.models import MentorProfile
from apps.taxonomy.models import Skill


class SessionStatus(models.TextChoices):
    REQUESTED = "REQUESTED", _("Requested")
    ACCEPTED = "ACCEPTED", _("Accepted")
    DECLINED = "DECLINED", _("Declined")
    COMPLETED = "COMPLETED", _("Completed")
    CANCELLED = "CANCELLED", _("Cancelled")
    NO_SHOW = "NO_SHOW", _("No show")


class SessionMode(models.TextChoices):
    ONLINE = "ONLINE", _("Online")
    ONSITE = "ONSITE", _("In person")


class MentorSession(BaseModel):
    mentor = models.ForeignKey(
        MentorProfile, on_delete=models.CASCADE, related_name="sessions"
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mentor_sessions"
    )
    topic = models.CharField(max_length=200)
    agenda = models.TextField(blank=True, max_length=2000)

    scheduled_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveSmallIntegerField(default=45)
    mode = models.CharField(
        max_length=8, choices=SessionMode.choices, default=SessionMode.ONLINE
    )
    meeting_link = models.URLField(blank=True)
    location = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=10, choices=SessionStatus.choices, default=SessionStatus.REQUESTED
    )
    notes = models.TextField(blank=True)
    declined_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "mentorship_session"
        ordering = ["-scheduled_at", "-created_at"]
        indexes = [
            models.Index(fields=["mentor", "status"]),
            models.Index(fields=["student", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.topic} · {self.status}"


class MentorFeedback(BaseModel):
    """Post-session feedback. Mentor-authored skill assessments feed
    SkillEvidence(source=MENTOR)."""

    session = models.ForeignKey(
        MentorSession, on_delete=models.CASCADE, related_name="feedback"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mentor_feedback"
    )
    rating = models.PositiveSmallIntegerField(default=5)
    comment = models.TextField(blank=True, max_length=2000)
    skills_assessed = models.ManyToManyField(
        Skill, blank=True, through="MentorSkillAssessment"
    )

    class Meta:
        db_table = "mentorship_feedback"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "author"], name="uniq_feedback_per_author"
            ),
            models.CheckConstraint(
                condition=models.Q(rating__gte=1, rating__lte=5),
                name="mentor_feedback_rating_range",
            ),
        ]


class MentorSkillAssessment(BaseModel):
    feedback = models.ForeignKey(
        MentorFeedback, on_delete=models.CASCADE, related_name="skill_assessments"
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="mentor_assessments"
    )
    score = models.PositiveSmallIntegerField(default=50)

    class Meta:
        db_table = "mentorship_skill_assessment"
        constraints = [
            models.UniqueConstraint(
                fields=["feedback", "skill"], name="uniq_mentor_skill_assessment"
            )
        ]


class ReviewStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    APPROVED = "APPROVED", _("Approved")
    CHANGES_REQUESTED = "CHANGES_REQUESTED", _("Changes requested")


class PlanReview(BaseModel):
    """Mentor or manager review of an AI-generated development plan.

    TZ §13: "AI-generated IDP ... manager/mentor tomonidan review qilinishi
    mumkin bo'lsin" — the plan is a proposal, not a verdict.
    """

    plan = models.ForeignKey(
        "idp.DevelopmentPlan", on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plan_reviews"
    )
    status = models.CharField(
        max_length=18, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
    )
    comment = models.TextField(blank=True, max_length=2000)

    class Meta:
        db_table = "mentorship_plan_review"
        ordering = ["-created_at"]
