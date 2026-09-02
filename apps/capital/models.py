"""Kapital Index — the nine axes from TZ §2.1 / §6."""

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel
from apps.taxonomy.models import CapitalDimension


class CapitalIndex(BaseModel):
    """Current 0-100 score on one capital axis for one user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="capital_indexes",
    )
    dimension = models.ForeignKey(
        CapitalDimension, on_delete=models.CASCADE, related_name="user_indexes"
    )
    score = models.PositiveSmallIntegerField(default=0)

    #: {"skills": 62.0, "activity": 40.0, "signals": [...], "config_version": "..."}
    breakdown = models.JSONField(default=dict, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "capital_index"
        ordering = ["dimension__order"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "dimension"], name="uniq_capital_user_dimension"
            ),
            models.CheckConstraint(
                condition=models.Q(score__gte=0, score__lte=100),
                name="capital_score_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} · {self.dimension.slug}: {self.score}"


class CapitalSnapshot(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="capital_snapshots",
    )
    dimension = models.ForeignKey(
        CapitalDimension, on_delete=models.CASCADE, related_name="snapshots"
    )
    score = models.PositiveSmallIntegerField()
    taken_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "capital_snapshot"
        ordering = ["taken_at"]
        indexes = [models.Index(fields=["user", "taken_at"])]


class CapitalWeightConfig(BaseModel):
    """Versioned weighting for the index.

    TZ §21 leaves "Kapital indeksini hisoblash metodologiyasi va weightlar" open;
    keeping it as data means the methodology can be calibrated during the pilot
    without redeploying.
    """

    version = models.CharField(max_length=32, unique=True)
    is_active = models.BooleanField(default=False)

    #: {"skill_component": 0.7, "activity_component": 0.3,
    #:  "dimension_weights": {"KNOWLEDGE": 1.0, ...}}
    weights = models.JSONField(default=dict)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="capital_configs",
    )

    class Meta:
        db_table = "capital_weight_config"
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="only_one_active_capital_config",
            )
        ]

    def __str__(self) -> str:
        return f"CapitalWeightConfig {self.version}{' (active)' if self.is_active else ''}"

    @classmethod
    def active(cls) -> "CapitalWeightConfig":
        config = cls.objects.filter(is_active=True).first()
        if config is None:
            config = cls.objects.create(
                version="default-1.0",
                is_active=True,
                weights=DEFAULT_CAPITAL_WEIGHTS,
                notes="Auto-created default. Calibrate during the pilot (TZ §21).",
            )
        return config


DEFAULT_CAPITAL_WEIGHTS: dict = {
    "skill_component": 0.7,
    "activity_component": 0.3,
    "dimension_weights": {
        "KNOWLEDGE": 1.0,
        "PROFESSIONAL": 1.0,
        "DIGITAL_AI": 1.0,
        "SOCIAL": 1.0,
        "ENTREPRENEURIAL": 1.0,
        "FINANCIAL": 1.0,
        "PERSONAL_ETHICAL": 1.0,
        "HEALTH": 1.0,
        "CIVIC": 1.0,
    },
}
