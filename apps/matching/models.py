"""Matching results and their configurable weights (prompt §20)."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel
from apps.jobs.models import Vacancy
from apps.taxonomy.models import Profession

#: Default weights. Deliberately *not* the prompt's 40/25/15/10/10 — that set
#: counts test and course results twice, once directly and once inside the
#: knowledge score they already produced (docs/01-ANALYSIS.md §3.1).
DEFAULT_WEIGHTS: dict[str, float] = {
    "coverage": 0.35,
    "knowledge": 0.25,
    "verification": 0.15,
    "experience": 0.15,
    "education": 0.05,
    "location": 0.05,
}

#: Kept so the two schemes can be compared on real data during the pilot.
LEGACY_PROMPT_WEIGHTS: dict[str, float] = {
    "coverage": 0.40,
    "knowledge": 0.25,
    "verification": 0.15,
    "experience": 0.10,
    "education": 0.05,
    "location": 0.05,
}


class MatchWeightProfile(BaseModel):
    name = models.CharField(max_length=64, unique=True)
    version = models.CharField(max_length=16, default="1.0")
    is_active = models.BooleanField(default=False)
    weights = models.JSONField(default=dict)
    #: Below this score a student is not notified about a vacancy — noise costs
    #: trust faster than it gains applications.
    min_score_to_notify = models.PositiveSmallIntegerField(default=70)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="match_profiles",
    )

    class Meta:
        db_table = "matching_weight_profile"
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="only_one_active_match_profile",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name}{' (active)' if self.is_active else ''}"

    @classmethod
    def active(cls) -> "MatchWeightProfile":
        profile = cls.objects.filter(is_active=True).first()
        if profile is None:
            profile = cls.objects.create(
                name="default",
                is_active=True,
                weights=DEFAULT_WEIGHTS,
                notes="Auto-created default weight profile.",
            )
        return profile

    def normalised_weights(self) -> dict[str, float]:
        """Guarantee the weights sum to 1 even if an admin mistypes one."""
        weights = {k: float(v) for k, v in (self.weights or DEFAULT_WEIGHTS).items()}
        total = sum(weights.values())
        if total <= 0:
            return DEFAULT_WEIGHTS
        return {k: v / total for k, v in weights.items()}


class MatchResult(BaseModel):
    """One student against one vacancy.

    Stored rather than computed on read: the employer's candidate list has to
    sort thousands of rows by score, and a per-row Python computation cannot be
    ordered by the database.
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="match_results"
    )
    vacancy = models.ForeignKey(
        Vacancy, on_delete=models.CASCADE, related_name="match_results"
    )

    overall_score = models.PositiveSmallIntegerField(default=0)
    coverage_score = models.PositiveSmallIntegerField(default=0)
    knowledge_score = models.PositiveSmallIntegerField(default=0)
    verification_score = models.PositiveSmallIntegerField(default=0)
    experience_score = models.PositiveSmallIntegerField(default=0)
    education_score = models.PositiveSmallIntegerField(default=0)
    location_score = models.PositiveSmallIntegerField(default=0)

    matched_skills = models.JSONField(default=list, blank=True)
    missing_skills = models.JSONField(default=list, blank=True)
    breakdown = models.JSONField(default=dict, blank=True)
    #: Fact-based reasons, localisable on the client. Never a bare percentage —
    #: TZ §13 and prompt §19 both require the "why".
    explanation = models.JSONField(default=list, blank=True)

    weight_profile = models.ForeignKey(
        MatchWeightProfile,
        on_delete=models.SET_NULL,
        null=True,
        related_name="results",
    )
    computed_at = models.DateTimeField(auto_now=True)
    is_stale = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "matching_result"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "vacancy"], name="uniq_match_student_vacancy"
            ),
            models.CheckConstraint(
                condition=models.Q(overall_score__gte=0, overall_score__lte=100),
                name="match_overall_score_range",
            ),
        ]
        indexes = [
            # Employer view: best candidates for a vacancy.
            models.Index(fields=["vacancy", "-overall_score"]),
            # Student view: best vacancies for a person.
            models.Index(fields=["student", "-overall_score"]),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} × {self.vacancy_id}: {self.overall_score}%"


class ProfessionMatch(BaseModel):
    """How ready a student is for a profession — drives Career Path (prompt §5)."""

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profession_matches",
    )
    profession = models.ForeignKey(
        Profession, on_delete=models.CASCADE, related_name="student_matches"
    )
    score = models.PositiveSmallIntegerField(default=0)
    matched_skills = models.JSONField(default=list, blank=True)
    missing_skills = models.JSONField(default=list, blank=True)
    breakdown = models.JSONField(default=dict, blank=True)
    computed_at = models.DateTimeField(auto_now=True)
    is_stale = models.BooleanField(default=False)

    class Meta:
        db_table = "matching_profession_match"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "profession"], name="uniq_profession_match"
            )
        ]
        indexes = [models.Index(fields=["student", "-score"])]
