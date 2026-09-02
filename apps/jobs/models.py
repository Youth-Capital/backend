"""Edu-Job: vacancies, applications and employment outcomes (prompt §11, §12)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.assessment.models import Test
from apps.common.enums import Language, ModerationStatus, RequirementLevel
from apps.common.models import BaseModel
from apps.profiles.models import EmployerProfile
from apps.taxonomy.models import Profession, Region, Skill


class EmploymentType(models.TextChoices):
    FULL_TIME = "FULL_TIME", _("Full time")
    PART_TIME = "PART_TIME", _("Part time")
    INTERNSHIP = "INTERNSHIP", _("Internship")
    CONTRACT = "CONTRACT", _("Contract")
    FREELANCE = "FREELANCE", _("Freelance")


class WorkMode(models.TextChoices):
    ONSITE = "ONSITE", _("On site")
    REMOTE = "REMOTE", _("Remote")
    HYBRID = "HYBRID", _("Hybrid")


class EducationRequirement(models.TextChoices):
    NONE = "NONE", _("No requirement")
    SCHOOL = "SCHOOL", _("Secondary school")
    COLLEGE = "COLLEGE", _("College / lyceum")
    UNIVERSITY = "UNIVERSITY", _("University degree")


class Vacancy(BaseModel):
    employer = models.ForeignKey(
        EmployerProfile, on_delete=models.CASCADE, related_name="vacancies"
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    responsibilities = models.TextField(blank=True)
    conditions = models.TextField(blank=True)
    language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.UZ
    )

    employment_type = models.CharField(
        max_length=12, choices=EmploymentType.choices, default=EmploymentType.FULL_TIME
    )
    work_mode = models.CharField(
        max_length=8, choices=WorkMode.choices, default=WorkMode.ONSITE
    )
    region = models.ForeignKey(
        Region, on_delete=models.SET_NULL, null=True, blank=True, related_name="vacancies"
    )
    city = models.CharField(max_length=120, blank=True)
    profession = models.ForeignKey(
        Profession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vacancies",
    )

    min_experience_months = models.PositiveSmallIntegerField(default=0)
    education_required = models.CharField(
        max_length=12,
        choices=EducationRequirement.choices,
        default=EducationRequirement.NONE,
    )

    salary_min = models.PositiveIntegerField(null=True, blank=True)
    salary_max = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="UZS")
    is_salary_public = models.BooleanField(default=True)

    positions_count = models.PositiveSmallIntegerField(default=1)
    deadline = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.DRAFT
    )
    moderation_note = models.TextField(blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderated_vacancies",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    views_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "jobs_vacancy"
        ordering = ["-published_at", "-created_at"]
        verbose_name_plural = "vacancies"
        indexes = [
            models.Index(fields=["status", "-published_at"]),
            models.Index(fields=["employer", "status"]),
            models.Index(fields=["region", "status"]),
            models.Index(fields=["profession", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(salary_max__isnull=True)
                    | models.Q(salary_min__isnull=True)
                    | models.Q(salary_max__gte=models.F("salary_min"))
                ),
                name="vacancy_salary_range_valid",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} · {self.employer.display_name}"

    @property
    def is_open(self) -> bool:
        from django.utils import timezone

        if self.status != ModerationStatus.PUBLISHED:
            return False
        if self.deadline and self.deadline < timezone.localdate():
            return False
        return True


class VacancySkill(BaseModel):
    vacancy = models.ForeignKey(
        Vacancy, on_delete=models.CASCADE, related_name="skill_links"
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.PROTECT, related_name="vacancy_links"
    )
    requirement = models.CharField(
        max_length=12,
        choices=RequirementLevel.choices,
        default=RequirementLevel.REQUIRED,
    )
    min_knowledge_score = models.PositiveSmallIntegerField(default=50)
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1.0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "jobs_vacancy_skill"
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vacancy", "skill"], name="uniq_vacancy_skill"
            )
        ]
        indexes = [models.Index(fields=["skill", "requirement"])]


class VacancyTest(BaseModel):
    """Employer screening attached to a vacancy.

    Kept separate from the global knowledge profile on purpose — see
    docs/01-ANALYSIS.md §3.2.
    """

    vacancy = models.ForeignKey(
        Vacancy, on_delete=models.CASCADE, related_name="screening_tests"
    )
    test = models.ForeignKey(Test, on_delete=models.PROTECT, related_name="vacancies")
    is_mandatory = models.BooleanField(default=False)

    class Meta:
        db_table = "jobs_vacancy_test"
        constraints = [
            models.UniqueConstraint(fields=["vacancy", "test"], name="uniq_vacancy_test")
        ]


class SavedVacancy(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_vacancies"
    )
    vacancy = models.ForeignKey(
        Vacancy, on_delete=models.CASCADE, related_name="saved_by"
    )

    class Meta:
        db_table = "jobs_saved_vacancy"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "vacancy"], name="uniq_saved_vacancy")
        ]


class ApplicationStatus(models.TextChoices):
    APPLIED = "APPLIED", _("Applied")
    UNDER_REVIEW = "UNDER_REVIEW", _("Under review")
    SHORTLISTED = "SHORTLISTED", _("Shortlisted")
    INTERVIEW = "INTERVIEW", _("Interview")
    OFFER = "OFFER", _("Offer")
    ACCEPTED = "ACCEPTED", _("Accepted")
    REJECTED = "REJECTED", _("Rejected")
    WITHDRAWN = "WITHDRAWN", _("Withdrawn")


#: Legal status transitions. Enforced in services so an application cannot jump
#: from APPLIED straight to ACCEPTED and leave the funnel analytics lying.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ApplicationStatus.APPLIED: {
        ApplicationStatus.UNDER_REVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.UNDER_REVIEW: {
        ApplicationStatus.SHORTLISTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.SHORTLISTED: {
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.INTERVIEW: {
        ApplicationStatus.OFFER,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.OFFER: {
        ApplicationStatus.ACCEPTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.ACCEPTED: set(),
    ApplicationStatus.REJECTED: set(),
    ApplicationStatus.WITHDRAWN: set(),
}


class Application(BaseModel):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="applications"
    )
    vacancy = models.ForeignKey(
        Vacancy, on_delete=models.PROTECT, related_name="applications"
    )
    cv = models.ForeignKey(
        "cv.CVDocument",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applications",
    )
    cover_letter = models.TextField(blank=True, max_length=4000)
    status = models.CharField(
        max_length=14,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.APPLIED,
    )
    #: Snapshot: the live match score drifts as the student learns, and a
    #: hiring decision must stay explainable against what was true that day.
    match_score_at_apply = models.PositiveSmallIntegerField(default=0)
    applied_at = models.DateTimeField(auto_now_add=True)
    status_changed_at = models.DateTimeField(auto_now_add=True)
    employer_note = models.TextField(blank=True)

    class Meta:
        db_table = "jobs_application"
        ordering = ["-applied_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "vacancy"], name="uniq_application_per_vacancy"
            )
        ]
        indexes = [
            models.Index(fields=["vacancy", "status"]),
            models.Index(fields=["student", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} -> {self.vacancy.title} [{self.status}]"

    @property
    def is_active(self) -> bool:
        return self.status not in {
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.ACCEPTED,
        }


class ApplicationEvent(BaseModel):
    """Immutable status trail — the audit answer to "why was this rejected?"."""

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="events"
    )
    from_status = models.CharField(max_length=14, blank=True)
    to_status = models.CharField(max_length=14)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="application_events",
    )
    note = models.TextField(blank=True)

    class Meta:
        db_table = "jobs_application_event"
        ordering = ["created_at"]


class InterviewMode(models.TextChoices):
    ONLINE = "ONLINE", _("Online")
    ONSITE = "ONSITE", _("On site")
    PHONE = "PHONE", _("Phone")


class InterviewStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED", _("Scheduled")
    COMPLETED = "COMPLETED", _("Completed")
    CANCELLED = "CANCELLED", _("Cancelled")
    NO_SHOW = "NO_SHOW", _("No show")


class Interview(BaseModel):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="interviews"
    )
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=45)
    mode = models.CharField(
        max_length=8, choices=InterviewMode.choices, default=InterviewMode.ONLINE
    )
    location = models.CharField(max_length=255, blank=True)
    meeting_link = models.URLField(blank=True)
    status = models.CharField(
        max_length=10, choices=InterviewStatus.choices, default=InterviewStatus.SCHEDULED
    )
    feedback = models.TextField(blank=True)

    class Meta:
        db_table = "jobs_interview"
        ordering = ["-scheduled_at"]


class PlacementStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("Active")
    ENDED = "ENDED", _("Ended")


class Placement(BaseModel):
    """Employment outcome — the KPI the TZ actually measures (§15).

    Applications and matches are activity metrics; the programme is judged on
    people getting and keeping work, so retention is tracked explicitly.
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="placements"
    )
    employer = models.ForeignKey(
        EmployerProfile, on_delete=models.PROTECT, related_name="placements"
    )
    vacancy = models.ForeignKey(
        Vacancy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="placements",
    )
    application = models.OneToOneField(
        Application,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="placement",
    )

    position = models.CharField(max_length=200)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=8, choices=PlacementStatus.choices, default=PlacementStatus.ACTIVE
    )

    retention_30 = models.BooleanField(null=True, blank=True)
    retention_90 = models.BooleanField(null=True, blank=True)
    retention_180 = models.BooleanField(null=True, blank=True)

    #: Only populated when the student granted Consent(INCOME_TRACKING).
    income_reported = models.PositiveIntegerField(null=True, blank=True)
    income_currency = models.CharField(max_length=3, default="UZS")

    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_placements",
    )

    class Meta:
        db_table = "jobs_placement"
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["employer", "status"]),
            models.Index(fields=["start_date"]),
        ]


class InviteStatus(models.TextChoices):
    PENDING = "PENDING", _("Waiting for a reply")
    ACCEPTED = "ACCEPTED", _("Accepted")
    DECLINED = "DECLINED", _("Declined")
    CANCELLED = "CANCELLED", _("Cancelled by the employer")


class InterviewInvite(BaseModel):
    """An employer asking a candidate to talk, before any application exists.

    Almost nobody in the candidate list has applied — that is the point of
    talent search, and it is why an interview cannot simply be scheduled. An
    `Interview` hangs off an `Application`, and an application is the student's
    act: creating one on their behalf would put a decision they never made into
    the funnel, and would hand the employer their identity without consent.

    So the invitation is its own thing. The employer states the intent, the
    platform carries it, and the student answers. Accepting is what creates the
    application — which keeps "applied" meaning what it says, and keeps the
    identity reveal tied to the student's own decision.

    A proposed time is optional: "we would like to talk" is a real message even
    before a calendar is open.
    """

    vacancy = models.ForeignKey(
        Vacancy, on_delete=models.CASCADE, related_name="interview_invites"
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="interview_invites",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_interview_invites",
    )

    message = models.TextField(blank=True, max_length=2000)
    proposed_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveSmallIntegerField(default=45)
    mode = models.CharField(
        max_length=8, choices=InterviewMode.choices, default=InterviewMode.ONLINE
    )
    location = models.CharField(max_length=255, blank=True)
    meeting_link = models.URLField(blank=True)

    status = models.CharField(
        max_length=10, choices=InviteStatus.choices, default=InviteStatus.PENDING
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    #: Why the student said no. Optional, and never shown as a rejection reason
    #: to anyone but the employer who asked.
    response_note = models.TextField(blank=True, max_length=1000)

    #: The score when the invitation went out. The live one drifts as the
    #: student learns, and "why did you invite them?" must stay answerable.
    match_score_at_invite = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "jobs_interview_invite"
        ordering = ["-created_at"]
        constraints = [
            # One open invitation per person per vacancy. Without this, a
            # frustrated employer clicking twice sends two, and the candidate
            # gets to accept a conversation they are already in.
            models.UniqueConstraint(
                fields=["vacancy", "student"],
                condition=models.Q(status="PENDING"),
                name="uniq_pending_invite_per_vacancy",
            )
        ]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["vacancy", "status"]),
        ]

    def __str__(self) -> str:
        return f"invite {self.student_id} -> {self.vacancy_id} [{self.status}]"

    @property
    def is_open(self) -> bool:
        return self.status == InviteStatus.PENDING
