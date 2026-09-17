"""Knowledge analytics (prompt §4).

Relationship to `profiles.UserSkill` — the two are deliberately different:

* ``UserSkill.proficiency`` — does the person *have* the skill at all. Includes
  self-declaration, so it answers "what is on their profile".
* ``KnowledgeScore.score`` — how well they actually *know* it, computed only
  from objective evidence (graded tests, completed course assessments,
  verified experience). Self-declaration is excluded on purpose: the prompt
  defines Knowledge Score as derived from "результатов тестов, quiz results,
  course progress, assessments", and a number anyone can set themselves is not
  an assessment.

Matching consumes them as separate, non-overlapping signals — see
docs/01-ANALYSIS.md §3.1.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.enums import ProficiencyBand
from apps.common.models import BaseModel
from apps.taxonomy.models import Skill


class KnowledgeScore(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="knowledge_scores",
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="knowledge_scores"
    )
    score = models.PositiveSmallIntegerField(default=0)
    confidence = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    evidence_count = models.PositiveSmallIntegerField(default=0)

    #: {"test": 88.0, "course": 70.0, "experience": 0.0, "weights": {...}}
    breakdown = models.JSONField(default=dict, blank=True)
    last_computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "knowledge_score"
        ordering = ["-score"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "skill"], name="uniq_knowledge_user_skill"
            ),
            models.CheckConstraint(
                condition=models.Q(score__gte=0, score__lte=100),
                name="knowledge_score_range",
            ),
        ]
        indexes = [models.Index(fields=["user", "-score"])]

    def __str__(self) -> str:
        return f"{self.user_id} · {self.skill.name}: {self.score}"

    @property
    def band(self) -> str:
        return ProficiencyBand.from_score(self.score)


class KnowledgeSnapshot(BaseModel):
    """Point-in-time score, so the UI can draw "progress over time"."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="knowledge_snapshots",
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="knowledge_snapshots"
    )
    score = models.PositiveSmallIntegerField()
    taken_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "knowledge_snapshot"
        ordering = ["taken_at"]
        indexes = [models.Index(fields=["user", "skill", "taken_at"])]


class KnowledgeConfig(BaseModel):
    """Versioned scoring formula.

    TZ §21 defers the methodology to the discovery phase, so it lives in the
    database and can be re-tuned without a code release.
    """

    version = models.CharField(max_length=32, unique=True)
    is_active = models.BooleanField(default=False)
    weights = models.JSONField(
        default=dict,
        help_text='e.g. {"test": 0.6, "course": 0.25, "experience": 0.15}',
    )
    decay_half_life_days = models.PositiveSmallIntegerField(default=540)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="knowledge_configs",
    )

    class Meta:
        db_table = "knowledge_config"
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="only_one_active_knowledge_config",
            )
        ]

    def __str__(self) -> str:
        return f"KnowledgeConfig {self.version}{' (active)' if self.is_active else ''}"

    @classmethod
    def active(cls) -> "KnowledgeConfig":
        config = cls.objects.filter(is_active=True).first()
        if config is None:
            config = cls.objects.create(
                version="default-1.0",
                is_active=True,
                weights=DEFAULT_KNOWLEDGE_WEIGHTS,
                notes="Auto-created default. Tune in the admin panel.",
            )
        return config


DEFAULT_KNOWLEDGE_WEIGHTS: dict[str, float] = {
    "TEST": 0.50,
    "COURSE": 0.25,
    "EXPERIENCE": 0.15,
    "EMPLOYER": 0.10,
}
