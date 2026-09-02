"""Enumerations shared across more than one app."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class Language(models.TextChoices):
    UZ = "uz", _("O'zbekcha")
    RU = "ru", _("Русский")
    EN = "en", _("English")


class Role(models.TextChoices):
    STUDENT = "STUDENT", _("Student")
    EMPLOYER = "EMPLOYER", _("Employer")
    MENTOR = "MENTOR", _("Mentor")
    ADMIN = "ADMIN", _("Administrator")


class ModerationStatus(models.TextChoices):
    """Lifecycle for anything an employer or partner authors.

    Nothing reaches learners without passing through PENDING_REVIEW — see
    docs/01-ANALYSIS.md §3.2 on why employer-authored content cannot be trusted
    to feed a candidate's global knowledge profile unmoderated.
    """

    DRAFT = "DRAFT", _("Draft")
    PENDING_REVIEW = "PENDING_REVIEW", _("Pending review")
    PUBLISHED = "PUBLISHED", _("Published")
    REJECTED = "REJECTED", _("Rejected")
    ARCHIVED = "ARCHIVED", _("Archived")


class VerificationStatus(models.TextChoices):
    UNVERIFIED = "UNVERIFIED", _("Unverified")
    PENDING = "PENDING", _("Pending")
    VERIFIED = "VERIFIED", _("Verified")
    REJECTED = "REJECTED", _("Rejected")


class RequirementLevel(models.TextChoices):
    REQUIRED = "REQUIRED", _("Required")
    PREFERRED = "PREFERRED", _("Preferred")


class ProficiencyBand(models.TextChoices):
    """Human-readable bands derived from the 0-100 scale."""

    NOVICE = "NOVICE", _("Novice")
    BASIC = "BASIC", _("Basic")
    INTERMEDIATE = "INTERMEDIATE", _("Intermediate")
    ADVANCED = "ADVANCED", _("Advanced")
    EXPERT = "EXPERT", _("Expert")

    @classmethod
    def from_score(cls, score: float) -> "ProficiencyBand":
        if score >= 90:
            return cls.EXPERT
        if score >= 75:
            return cls.ADVANCED
        if score >= 50:
            return cls.INTERMEDIATE
        if score >= 25:
            return cls.BASIC
        return cls.NOVICE


class EvidenceSource(models.TextChoices):
    """Where a claim about a skill came from.

    The ordering matters: a self-declared skill must never weigh as much as one
    an employer verified, otherwise matching rewards confident self-reporting
    (docs/01-ANALYSIS.md §3.3).
    """

    SELF = "SELF", _("Self-declared")
    COURSE = "COURSE", _("Course completion")
    EXPERIENCE = "EXPERIENCE", _("Work experience")
    MENTOR = "MENTOR", _("Mentor assessment")
    TEST = "TEST", _("Test result")
    EMPLOYER = "EMPLOYER", _("Employer verification")


#: Confidence multiplier per evidence source (0-1).
EVIDENCE_WEIGHTS: dict[str, float] = {
    EvidenceSource.SELF: 0.35,
    EvidenceSource.COURSE: 0.65,
    EvidenceSource.EXPERIENCE: 0.70,
    EvidenceSource.MENTOR: 0.85,
    EvidenceSource.TEST: 0.90,
    EvidenceSource.EMPLOYER: 1.00,
}


class Priority(models.TextChoices):
    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")
