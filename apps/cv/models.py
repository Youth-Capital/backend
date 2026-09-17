"""CV, portfolio and the public Youth Passport (prompt §10, TZ §8.13)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import Language
from apps.common.models import BaseModel
from apps.common.validators import validate_image_upload
from apps.taxonomy.models import Skill

#: Blocks a CV can contain. The document stores *which* blocks are shown and in
#: what order — never a copy of the data itself, so a CV can never go stale
#: relative to the profile it describes.
DEFAULT_SECTIONS: list[dict] = [
    {"key": "summary", "enabled": True, "order": 0},
    {"key": "education", "enabled": True, "order": 1},
    {"key": "skills", "enabled": True, "order": 2},
    {"key": "experience", "enabled": True, "order": 3},
    {"key": "projects", "enabled": True, "order": 4},
    {"key": "courses", "enabled": True, "order": 5},
    {"key": "certificates", "enabled": True, "order": 6},
    {"key": "achievements", "enabled": False, "order": 7},
    {"key": "languages", "enabled": True, "order": 8},
    {"key": "contacts", "enabled": True, "order": 9},
]


class CVTemplate(models.TextChoices):
    CLASSIC = "CLASSIC", _("Classic")
    MODERN = "MODERN", _("Modern")
    COMPACT = "COMPACT", _("Compact")


class CVDocument(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cvs"
    )
    title = models.CharField(max_length=160, default="My CV")
    language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.UZ
    )
    template = models.CharField(
        max_length=10, choices=CVTemplate.choices, default=CVTemplate.MODERN
    )
    headline = models.CharField(max_length=200, blank=True)
    summary = models.TextField(blank=True, max_length=2000)
    sections_config = models.JSONField(default=list, blank=True)
    #: Optional narrowing — e.g. only show skills relevant to one profession.
    target_profession = models.ForeignKey(
        "taxonomy.Profession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cvs",
    )
    is_primary = models.BooleanField(default=False)

    #: Cached CV quality rating (apps/cv/rating.py). Stored rather than always
    #: computed because a candidate list ranks fifty CVs at once, and six
    #: queries per candidate is not a list — it is a timeout. The breakdown
    #: travels with the number so the employer sees what it is made of.
    quality_score = models.PositiveSmallIntegerField(default=0)
    quality_breakdown = models.JSONField(default=dict, blank=True)
    quality_computed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cv_document"
        ordering = ["-is_primary", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_primary=True),
                name="one_primary_cv_per_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.user_id})"

    def save(self, *args, **kwargs):
        if not self.sections_config:
            self.sections_config = DEFAULT_SECTIONS
        super().save(*args, **kwargs)

    @property
    def enabled_sections(self) -> list[str]:
        return [
            section["key"]
            for section in sorted(
                self.sections_config, key=lambda s: s.get("order", 0)
            )
            if section.get("enabled")
        ]


class PortfolioItemType(models.TextChoices):
    PROJECT = "PROJECT", _("Project")
    ARTICLE = "ARTICLE", _("Article")
    DESIGN = "DESIGN", _("Design")
    CODE = "CODE", _("Code repository")
    VIDEO = "VIDEO", _("Video")
    OTHER = "OTHER", _("Other")


class PortfolioItem(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portfolio_items"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, max_length=3000)
    type = models.CharField(
        max_length=10,
        choices=PortfolioItemType.choices,
        default=PortfolioItemType.PROJECT,
    )
    url = models.URLField(blank=True)
    file = models.FileField(upload_to="portfolio/items/", blank=True, null=True)
    cover = models.ImageField(
        upload_to="portfolio/covers/",
        blank=True,
        null=True,
        validators=[validate_image_upload],
    )
    skills = models.ManyToManyField(Skill, blank=True, related_name="portfolio_items")
    experience = models.ForeignKey(
        "experience.Experience",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portfolio_items",
    )
    order = models.PositiveSmallIntegerField(default=0)
    is_public = models.BooleanField(default=True)

    class Meta:
        db_table = "cv_portfolio_item"
        ordering = ["order", "-created_at"]

    def __str__(self) -> str:
        return self.title


class PublicProfile(BaseModel):
    """Youth Passport — the shareable, consent-gated public view.

    Off by default. Nothing about a young person becomes publicly addressable
    until they turn it on (TZ §12, minimal disclosure).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="public_profile"
    )
    public_slug = models.SlugField(max_length=64, unique=True)
    is_public = models.BooleanField(default=False)
    #: {"skills": true, "experience": true, "contacts": false, ...}
    visible_blocks = models.JSONField(default=dict, blank=True)
    views_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "cv_public_profile"

    def __str__(self) -> str:
        return self.public_slug
