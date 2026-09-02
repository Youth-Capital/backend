"""Experience, projects and achievements (prompt §9)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.enums import VerificationStatus
from apps.common.models import BaseModel
from apps.taxonomy.models import Skill


class ExperienceType(models.TextChoices):
    INTERNSHIP = "INTERNSHIP", _("Internship")
    WORK = "WORK", _("Work experience")
    FREELANCE = "FREELANCE", _("Freelance")
    VOLUNTEER = "VOLUNTEER", _("Volunteering")
    PROJECT = "PROJECT", _("Project")
    COMPETITION = "COMPETITION", _("Competition")
    HACKATHON = "HACKATHON", _("Hackathon")
    ACHIEVEMENT = "ACHIEVEMENT", _("Achievement")


#: Types that count as professional tenure when matching against a vacancy's
#: `min_experience_months`. A hackathon is valuable but is not employment.
TENURE_TYPES = frozenset(
    {ExperienceType.WORK, ExperienceType.INTERNSHIP, ExperienceType.FREELANCE}
)


class Experience(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="experiences"
    )
    type = models.CharField(
        max_length=12, choices=ExperienceType.choices, default=ExperienceType.WORK
    )
    title = models.CharField(max_length=200)
    organization = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True, max_length=4000)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=False)

    location = models.CharField(max_length=160, blank=True)
    url = models.URLField(blank=True)

    verification_status = models.CharField(
        max_length=12,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_experiences",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "experience_entry"
        ordering = ["-is_current", "-start_date", "-created_at"]
        indexes = [
            models.Index(fields=["user", "type"]),
            models.Index(fields=["user", "-start_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(end_date__isnull=True)
                    | models.Q(start_date__isnull=True)
                    | models.Q(end_date__gte=models.F("start_date"))
                ),
                name="experience_end_after_start",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} @ {self.organization}".strip(" @")

    @property
    def duration_months(self) -> int:
        """Whole months between start and end (or today, if ongoing).

        Replaces the prompt's unquantifiable "Experience: High / Medium"
        (docs/01-ANALYSIS.md §3.4).
        """
        if not self.start_date:
            return 0
        end = self.end_date or timezone.localdate()
        if end < self.start_date:
            return 0
        months = (end.year - self.start_date.year) * 12 + (
            end.month - self.start_date.month
        )
        if end.day < self.start_date.day:
            months -= 1
        return max(months, 0)

    @property
    def counts_as_tenure(self) -> bool:
        return self.type in TENURE_TYPES


class ExperienceSkill(BaseModel):
    experience = models.ForeignKey(
        Experience, on_delete=models.CASCADE, related_name="skill_links"
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.PROTECT, related_name="experience_links"
    )

    class Meta:
        db_table = "experience_skill"
        constraints = [
            models.UniqueConstraint(
                fields=["experience", "skill"], name="uniq_experience_skill"
            )
        ]


class AssetType(models.TextChoices):
    IMAGE = "IMAGE", _("Image")
    FILE = "FILE", _("File")
    LINK = "LINK", _("Link")
    VIDEO = "VIDEO", _("Video")


class ProjectAsset(BaseModel):
    experience = models.ForeignKey(
        Experience, on_delete=models.CASCADE, related_name="assets"
    )
    type = models.CharField(
        max_length=8, choices=AssetType.choices, default=AssetType.LINK
    )
    file = models.FileField(upload_to="portfolio/", blank=True, null=True)
    url = models.URLField(blank=True)
    caption = models.CharField(max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "experience_project_asset"
        ordering = ["order", "created_at"]
