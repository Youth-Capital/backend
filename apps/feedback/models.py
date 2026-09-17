"""Platform feedback — what the people using this thing actually think.

Distinct from the feedback already in the codebase, and deliberately so:
``AIFeedback`` rates one suggestion and course ratings rate one course.
Neither of them answers "is the platform working
for the people on it", which is the question a programme with public
accountability has to be able to answer.

Three models, one job each:

``ReviewCampaign`` — a deliberate ask. An admin decides who is invited and
when, and the campaign keeps the count of what came back.

``ReviewRequest`` — one invitation to one person. It exists so the platform
knows who was already asked: without it, an auto-trigger and a campaign both
firing in the same week means two notifications about the same thing, and a
person who has been nagged is a person who stops reading notifications.

``PlatformReview`` — the answer. Pros and cons are separate fields on purpose:
one box labelled "feedback" collects praise from people who like it and
complaints from people who do not, and nothing usable from anyone in between.
Asking for both makes the satisfied user name a weakness and the frustrated one
name a strength — which is where the useful material is.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import ModerationStatus, Role
from apps.common.models import BaseModel


class ReviewTrigger(models.TextChoices):
    """Why this person was asked."""

    CAMPAIGN = "CAMPAIGN", _("Admin campaign")
    COURSE_COMPLETED = "COURSE_COMPLETED", _("Finished a course")
    PLACEMENT = "PLACEMENT", _("Started a job through the platform")
    HIRED_CANDIDATE = "HIRED_CANDIDATE", _("Hired through the platform")
    MANUAL = "MANUAL", _("Wrote a review unprompted")


class CampaignStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    RUNNING = "RUNNING", _("Running")
    FINISHED = "FINISHED", _("Finished")


class ReviewCampaign(BaseModel):
    title = models.CharField(max_length=200)
    #: Shown above the form so people know why they are being asked now.
    message = models.TextField(blank=True, max_length=1000)
    #: Which roles to invite. Empty means everyone.
    audience_roles = models.JSONField(default=list, blank=True)
    #: Skip accounts registered after this — someone who signed up yesterday
    #: has no opinion worth collecting, and asking makes the platform look
    #: like it is fishing for stars.
    min_account_age_days = models.PositiveSmallIntegerField(default=14)

    status = models.CharField(
        max_length=8, choices=CampaignStatus.choices, default=CampaignStatus.DRAFT
    )
    launched_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)

    invited_count = models.PositiveIntegerField(default=0)
    responded_count = models.PositiveIntegerField(default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_campaigns",
    )

    class Meta:
        db_table = "feedback_review_campaign"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    @property
    def response_rate(self) -> int:
        if not self.invited_count:
            return 0
        return round(100 * self.responded_count / self.invited_count)

    def targets_role(self, role: str) -> bool:
        return not self.audience_roles or role in self.audience_roles


class ReviewRequest(BaseModel):
    """One ask, to one person. The record that stops a second one."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_requests"
    )
    campaign = models.ForeignKey(
        ReviewCampaign,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="requests",
    )
    trigger = models.CharField(
        max_length=18, choices=ReviewTrigger.choices, default=ReviewTrigger.CAMPAIGN
    )
    #: What prompted it — the course, the placement, the session.
    ref_type = models.CharField(max_length=40, blank=True)
    ref_id = models.UUIDField(null=True, blank=True)

    notified_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    #: Explicit refusal. Honoured for a cooling-off period rather than
    #: forever — "not now" and "never" are different answers.
    dismissed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "feedback_review_request"
        ordering = ["-created_at"]
        constraints = [
            # One open ask per person per campaign. Re-running a launch must
            # not re-invite the people who already got it.
            models.UniqueConstraint(
                fields=["user", "campaign"],
                condition=models.Q(campaign__isnull=False),
                name="uniq_campaign_request_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["user", "responded_at"]),
            models.Index(fields=["campaign", "responded_at"]),
        ]

    def __str__(self) -> str:
        return f"ask {self.user_id} ({self.trigger})"

    @property
    def is_open(self) -> bool:
        return self.responded_at is None and self.dismissed_at is None


class PlatformReview(BaseModel):
    """What one participant thinks of the platform.

    ``role_at_review`` is denormalised on purpose: a person's role can change,
    and "the employers rate us 3.4" must keep meaning what it meant on the day
    the review was written.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="platform_reviews",
    )
    request = models.OneToOneField(
        ReviewRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review",
    )
    campaign = models.ForeignKey(
        ReviewCampaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
    )
    role_at_review = models.CharField(max_length=16, choices=Role.choices)
    trigger = models.CharField(
        max_length=18, choices=ReviewTrigger.choices, default=ReviewTrigger.MANUAL
    )

    rating = models.PositiveSmallIntegerField()
    #: "How likely are you to recommend it" on the standard 0-10 scale. Kept
    #: separate from `rating`: satisfaction and advocacy are different
    #: questions and people answer them differently.
    nps_score = models.PositiveSmallIntegerField(null=True, blank=True)

    pros = models.TextField(blank=True, max_length=2000)
    cons = models.TextField(blank=True, max_length=2000)
    suggestion = models.TextField(blank=True, max_length=2000)

    #: Published reviews may be shown publicly; everything starts pending, for
    #: the same reason every other user-authored text on this platform does.
    status = models.CharField(
        max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.PENDING_REVIEW
    )
    moderation_note = models.TextField(blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderated_reviews",
    )
    #: Signing it is the author's choice. An anonymous review still carries the
    #: role, because "an employer said this" is the part that makes it useful.
    is_anonymous = models.BooleanField(default=False)
    #: Somebody read it and acted. Closes the loop the campaign opened.
    admin_response = models.TextField(blank=True, max_length=2000)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="answered_reviews",
    )

    class Meta:
        db_table = "feedback_platform_review"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rating__gte=1, rating__lte=5),
                name="platform_review_rating_range",
            ),
            models.CheckConstraint(
                condition=models.Q(nps_score__isnull=True)
                | models.Q(nps_score__gte=0, nps_score__lte=10),
                name="platform_review_nps_range",
            ),
        ]
        indexes = [
            models.Index(fields=["role_at_review", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["rating"]),
        ]

    def __str__(self) -> str:
        return f"{self.role_at_review} · {self.rating}★"

    @property
    def nps_bucket(self) -> str:
        if self.nps_score is None:
            return "UNKNOWN"
        if self.nps_score >= 9:
            return "PROMOTER"
        if self.nps_score >= 7:
            return "PASSIVE"
        return "DETRACTOR"
