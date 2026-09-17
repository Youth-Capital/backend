"""Centralised reference data.

Skills, professions and capital dimensions are admin-managed (prompt §21).
If users could mint skills freely the taxonomy would fragment into "Python",
"python3" and "Пайтон", and matching would quietly stop working.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import RequirementLevel
from apps.common.models import BaseModel, TranslatableNameMixin


class Region(TranslatableNameMixin, BaseModel):
    """Administrative region, optionally nested (viloyat -> tuman)."""

    code = models.CharField(max_length=16, unique=True)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "taxonomy_region"
        ordering = ["code"]


class SkillCategory(TranslatableNameMixin, BaseModel):
    """Tree of skill groupings — Technology > Programming > Python."""

    slug = models.SlugField(max_length=120, unique=True)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    icon = models.CharField(max_length=64, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    #: Behavioural competencies rather than technical ability. Flagged on the
    #: category, not the skill, because softness is a property of the whole
    #: branch — and inferring it from a slug ("soft-skills") would break the
    #: first time somebody renames it. Children inherit it; see
    #: :func:`soft_skill_ids`.
    is_soft_skill = models.BooleanField(default=False)

    class Meta:
        db_table = "taxonomy_skill_category"
        ordering = ["order", "name_uz"]
        verbose_name_plural = "skill categories"

    @property
    def is_soft(self) -> bool:
        node, guard = self, 0
        while node is not None and guard < 10:
            if node.is_soft_skill:
                return True
            node = node.parent
            guard += 1
        return False

    @property
    def path(self) -> str:
        parts, node, guard = [], self, 0
        while node is not None and guard < 10:
            parts.append(node.name)
            node = node.parent
            guard += 1
        return " / ".join(reversed(parts))


class Skill(TranslatableNameMixin, BaseModel):
    slug = models.SlugField(max_length=120, unique=True)
    category = models.ForeignKey(
        SkillCategory, on_delete=models.PROTECT, related_name="skills"
    )
    #: Free-text synonyms so search finds "JS" and "ECMAScript" for JavaScript.
    aliases = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "taxonomy_skill"
        ordering = ["name_uz"]
        indexes = [models.Index(fields=["category", "is_active"])]


class CapitalDimensionSlug(models.TextChoices):
    """The nine capital types from TZ §2.1."""

    KNOWLEDGE = "KNOWLEDGE", _("Bilim kapitali")
    PROFESSIONAL = "PROFESSIONAL", _("Kasbiy kapital")
    DIGITAL_AI = "DIGITAL_AI", _("Raqamli va AI kapitali")
    SOCIAL = "SOCIAL", _("Ijtimoiy kapital")
    ENTREPRENEURIAL = "ENTREPRENEURIAL", _("Tadbirkorlik kapitali")
    FINANCIAL = "FINANCIAL", _("Moliyaviy kapital")
    PERSONAL_ETHICAL = "PERSONAL_ETHICAL", _("Shaxsiy va axloqiy kapital")
    HEALTH = "HEALTH", _("Sog'lom hayot kapitali")
    CIVIC = "CIVIC", _("Fuqarolik kapitali")


class CapitalDimension(TranslatableNameMixin, BaseModel):
    """One axis of the Kapital Index (TZ §6)."""

    slug = models.CharField(
        max_length=32, choices=CapitalDimensionSlug.choices, unique=True
    )
    icon = models.CharField(max_length=64, blank=True)
    color = models.CharField(max_length=16, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    default_weight = models.DecimalField(max_digits=4, decimal_places=3, default=1.0)

    class Meta:
        db_table = "taxonomy_capital_dimension"
        ordering = ["order"]


class SkillDimension(BaseModel):
    """How much a skill contributes to a capital axis.

    This table is the bridge between the prompt's Skills system and the TZ's
    Kapital Index — without it the nine axes have nothing to be computed from.
    """

    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="dimension_links"
    )
    dimension = models.ForeignKey(
        CapitalDimension, on_delete=models.CASCADE, related_name="skill_links"
    )
    weight = models.DecimalField(max_digits=4, decimal_places=3, default=1.0)

    class Meta:
        db_table = "taxonomy_skill_dimension"
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "dimension"], name="uniq_skill_dimension"
            )
        ]


class DemandLevel(models.TextChoices):
    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")


class Profession(TranslatableNameMixin, BaseModel):
    slug = models.SlugField(max_length=120, unique=True)
    category = models.ForeignKey(
        SkillCategory,
        on_delete=models.PROTECT,
        related_name="professions",
        null=True,
        blank=True,
    )
    icon = models.CharField(max_length=64, blank=True)
    demand_level = models.CharField(
        max_length=8, choices=DemandLevel.choices, default=DemandLevel.MEDIUM
    )
    median_salary = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="UZS")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "taxonomy_profession"
        ordering = ["name_uz"]

    @property
    def required_skills(self):
        return self.skill_links.filter(requirement=RequirementLevel.REQUIRED)

    @property
    def recommended_skills(self):
        return self.skill_links.filter(requirement=RequirementLevel.PREFERRED)


class ProfessionSkill(BaseModel):
    profession = models.ForeignKey(
        Profession, on_delete=models.CASCADE, related_name="skill_links"
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.PROTECT, related_name="profession_links"
    )
    requirement = models.CharField(
        max_length=12,
        choices=RequirementLevel.choices,
        default=RequirementLevel.REQUIRED,
    )
    min_proficiency = models.PositiveSmallIntegerField(default=50)
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1.0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "taxonomy_profession_skill"
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["profession", "skill"], name="uniq_profession_skill"
            ),
            models.CheckConstraint(
                condition=models.Q(min_proficiency__gte=0, min_proficiency__lte=100),
                name="profession_skill_proficiency_range",
            ),
        ]
        indexes = [models.Index(fields=["profession", "requirement"])]

    def __str__(self) -> str:
        return f"{self.profession.name} · {self.skill.name} ({self.requirement})"
