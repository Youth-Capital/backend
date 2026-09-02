"""AI service abstraction (prompt §13).

The platform is never wired to one provider. Everything goes through
``AIService``; the default implementation is deterministic and needs no network
access, no API key and no budget, so the MVP works out of the box and can be
tested. Swapping in an LLM is a configuration change (``AIProviderConfig``),
not a refactor.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from .models import (
    AIProvider,
    AIProviderConfig,
    AIRecommendation,
    AIRequestLog,
    AIRequestStatus,
    AIUseCase,
    RecommendationType,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class Recommendation:
    type: str
    ref_id: str | None
    title: str
    score: int
    reason_code: str
    reason_data: dict = field(default_factory=dict)
    ref_type: str = ""


@dataclass
class SkillGapReport:
    profession: str
    readiness: int
    matching: list[dict] = field(default_factory=list)
    partial: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    next_actions: list[dict] = field(default_factory=list)


@dataclass
class KnowledgeInsight:
    average_score: int
    strongest: list[dict] = field(default_factory=list)
    weakest: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


@dataclass
class Explanation:
    summary_code: str
    reasons: list[dict] = field(default_factory=list)
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
class AIService(ABC):
    """The contract every provider implements.

    Method names follow the prompt's `AIService` sketch so the two stay
    recognisably the same object.
    """

    provider: str = AIProvider.RULE_BASED
    model: str = ""

    @abstractmethod
    def analyze_knowledge(self, user) -> KnowledgeInsight: ...

    @abstractmethod
    def analyze_skills(self, user, target) -> SkillGapReport: ...

    @abstractmethod
    def generate_career_recommendations(self, user, limit: int = 5) -> list[Recommendation]: ...

    @abstractmethod
    def calculate_match(self, student, vacancy): ...

    @abstractmethod
    def recommend_courses(self, user, limit: int = 5) -> list[Recommendation]: ...

    @abstractmethod
    def recommend_vacancies(self, user, limit: int = 5) -> list[Recommendation]: ...

    @abstractmethod
    def recommend_mentors(self, user, limit: int = 3) -> list[Recommendation]: ...

    @abstractmethod
    def explain_candidate(self, student, vacancy) -> Explanation: ...

    @abstractmethod
    def improve_cv(self, cv, vacancy=None) -> list[dict]: ...


# ---------------------------------------------------------------------------
# Logging wrapper
# ---------------------------------------------------------------------------
@contextmanager
def _logged(use_case: str, *, user=None, provider: str, model: str, payload: dict):
    """Record every AI call: provider, model, prompt version, latency (TZ §13).

    The prompt content is hashed rather than stored — these payloads describe a
    young person's profile, and an operations log is the wrong home for that.
    """
    started = time.monotonic()
    serialised = json.dumps(payload, sort_keys=True, default=str)
    entry = AIRequestLog(
        user=user if getattr(user, "pk", None) else None,
        use_case=use_case,
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        input_hash=hashlib.sha256(serialised.encode()).hexdigest(),
        input_preview=serialised[:500] if settings.AI_LOG_PROMPT_CONTENT else "",
    )
    try:
        yield entry
        entry.status = AIRequestStatus.SUCCESS
    except Exception as exc:
        entry.status = AIRequestStatus.ERROR
        entry.error_code = type(exc).__name__[:64]
        raise
    finally:
        entry.latency_ms = int((time.monotonic() - started) * 1000)
        try:
            entry.save()
        except Exception:  # pragma: no cover
            logger.exception("Failed to persist AIRequestLog")


# ---------------------------------------------------------------------------
# Default implementation
# ---------------------------------------------------------------------------
class RuleBasedAIService(AIService):
    """Deterministic recommendations built on the platform's own signals.

    Not a placeholder: it reads the same skill, knowledge and matching data an
    LLM would be given, and produces explanations from facts rather than
    prose. It is auditable, free, offline, and its output is reproducible in
    tests — properties a language model cannot offer.
    """

    provider = AIProvider.RULE_BASED

    # -- analysis --------------------------------------------------------
    def analyze_knowledge(self, user) -> KnowledgeInsight:
        from apps.knowledge.services import get_knowledge_overview

        with _logged(
            AIUseCase.LEARNING,
            user=user,
            provider=self.provider,
            model=self.model,
            payload={"user": str(user.id)},
        ):
            overview = get_knowledge_overview(user, limit=50)
            topics = overview["topics"]
            strongest = topics[:3]
            weakest = sorted(topics, key=lambda t: t["score"])[:3]

            notes = []
            for topic in weakest:
                if topic["score"] < 50:
                    notes.append(
                        {
                            "code": "weak_topic",
                            "data": {"skill": topic["skill"], "score": topic["score"]},
                        }
                    )
            low_confidence = [t for t in topics if t["confidence"] < 0.6]
            if low_confidence:
                notes.append(
                    {
                        "code": "needs_verification",
                        "data": {"skills": [t["skill"] for t in low_confidence[:5]]},
                    }
                )

            return KnowledgeInsight(
                average_score=overview["average_score"],
                strongest=strongest,
                weakest=weakest,
                notes=notes,
            )

    def analyze_skills(self, user, target) -> SkillGapReport:
        from apps.profiles.services import get_skill_gap

        with _logged(
            AIUseCase.SKILL_GAP,
            user=user,
            provider=self.provider,
            model=self.model,
            payload={"user": str(user.id), "target": str(getattr(target, "id", target))},
        ):
            gap = get_skill_gap(user, target)
            next_actions = [
                {
                    "code": "learn_skill",
                    "skill": entry["skill"],
                    "skill_id": entry["skill_id"],
                    "from": entry["current_level"],
                    "to": entry["required_level"],
                }
                for entry in (gap["missing_skills"] + gap["partial_skills"])[:3]
            ]
            return SkillGapReport(
                profession=gap["profession"],
                readiness=gap["readiness"],
                matching=gap["matching_skills"],
                partial=gap["partial_skills"],
                missing=gap["missing_skills"],
                next_actions=next_actions,
            )

    # -- recommendations -------------------------------------------------
    def generate_career_recommendations(self, user, limit: int = 5) -> list[Recommendation]:
        from apps.matching.services import top_professions

        with _logged(
            AIUseCase.CAREER,
            user=user,
            provider=self.provider,
            model=self.model,
            payload={"user": str(user.id)},
        ):
            return [
                Recommendation(
                    type=RecommendationType.PROFESSION,
                    ref_type="Profession",
                    ref_id=str(match.profession_id),
                    title=match.profession.name,
                    score=match.score,
                    reason_code="profession_readiness",
                    reason_data={
                        "readiness": match.score,
                        "required_met": match.breakdown.get("required_met", 0),
                        "required_total": match.breakdown.get("required_total", 0),
                        "missing": [m["skill"] for m in match.missing_skills[:3]],
                    },
                )
                for match in top_professions(user, limit=limit)
            ]

    def recommend_courses(self, user, limit: int = 5) -> list[Recommendation]:
        """Courses that close the widest gap toward the user's target."""
        from apps.common.enums import ModerationStatus
        from apps.learning.models import Course, Enrollment
        from apps.profiles.services import get_skill_gap

        with _logged(
            AIUseCase.LEARNING,
            user=user,
            provider=self.provider,
            model=self.model,
            payload={"user": str(user.id), "limit": limit},
        ):
            profile = getattr(user, "student_profile", None)
            profession = getattr(profile, "target_profession", None)
            if profession is None:
                return []

            gap = get_skill_gap(user, profession)
            wanted = {
                entry["skill_id"]: entry
                for entry in (gap["missing_skills"] + gap["partial_skills"])
            }
            if not wanted:
                return []

            enrolled = set(
                Enrollment.objects.filter(user=user).values_list("course_id", flat=True)
            )
            courses = (
                Course.objects.filter(
                    status=ModerationStatus.PUBLISHED,
                    skill_links__skill_id__in=list(wanted.keys()),
                )
                .exclude(id__in=enrolled)
                .prefetch_related("skill_links__skill")
                .distinct()
            )

            scored: list[Recommendation] = []
            for course in courses:
                covered = [
                    link.skill
                    for link in course.skill_links.all()
                    if str(link.skill_id) in wanted
                ]
                if not covered:
                    continue
                # Value = how many gaps it closes, nudged by proven quality.
                score = min(100, 40 + 20 * len(covered) + round(float(course.rating_avg) * 4))
                scored.append(
                    Recommendation(
                        type=RecommendationType.COURSE,
                        ref_type="Course",
                        ref_id=str(course.id),
                        title=course.title,
                        score=score,
                        reason_code="closes_skill_gap",
                        reason_data={
                            "skills": [s.name for s in covered],
                            "profession": profession.name,
                            "level": course.level,
                        },
                    )
                )
            scored.sort(key=lambda r: r.score, reverse=True)
            return scored[:limit]

    def recommend_vacancies(self, user, limit: int = 5) -> list[Recommendation]:
        from apps.jobs.models import Application
        from apps.matching.models import MatchResult
        from apps.matching.services import recompute_matches_for_student

        with _logged(
            AIUseCase.CAREER,
            user=user,
            provider=self.provider,
            model=self.model,
            payload={"user": str(user.id), "limit": limit},
        ):
            applied = set(
                Application.objects.filter(student=user).values_list(
                    "vacancy_id", flat=True
                )
            )
            matches = MatchResult.objects.filter(student=user).exclude(
                vacancy_id__in=applied
            )
            if not matches.exists():
                recompute_matches_for_student(user, limit=50)
                matches = MatchResult.objects.filter(student=user).exclude(
                    vacancy_id__in=applied
                )

            return [
                Recommendation(
                    type=RecommendationType.VACANCY,
                    ref_type="Vacancy",
                    ref_id=str(match.vacancy_id),
                    title=match.vacancy.title,
                    score=match.overall_score,
                    reason_code="match_score",
                    reason_data={
                        "company": match.vacancy.employer.display_name,
                        "matched": [s["skill"] for s in match.matched_skills[:4]],
                        "missing": [s["skill"] for s in match.missing_skills[:3]],
                        "explanation": match.explanation,
                    },
                )
                for match in matches.select_related(
                    "vacancy", "vacancy__employer"
                ).order_by("-overall_score")[:limit]
            ]

    def recommend_mentors(self, user, limit: int = 3) -> list[Recommendation]:
        from apps.common.enums import VerificationStatus
        from apps.profiles.models import MentorProfile
        from apps.profiles.services import get_skill_gap

        with _logged(
            AIUseCase.CAREER,
            user=user,
            provider=self.provider,
            model=self.model,
            payload={"user": str(user.id)},
        ):
            profile = getattr(user, "student_profile", None)
            profession = getattr(profile, "target_profession", None)
            if profession is None:
                return []

            gap = get_skill_gap(user, profession)
            needed = [e["skill_id"] for e in gap["missing_skills"] + gap["partial_skills"]]

            mentors = (
                MentorProfile.objects.filter(
                    verification_status=VerificationStatus.VERIFIED,
                    accepting_students=True,
                )
                .filter(expertise__id__in=needed)
                .distinct()
                .order_by("-rating_avg")[:limit]
            )
            return [
                Recommendation(
                    type=RecommendationType.MENTOR,
                    ref_type="MentorProfile",
                    ref_id=str(mentor.id),
                    title=mentor.full_name or mentor.headline,
                    score=min(100, 60 + int(float(mentor.rating_avg) * 8)),
                    reason_code="mentor_expertise_match",
                    reason_data={
                        "headline": mentor.headline,
                        "rating": float(mentor.rating_avg),
                        "sessions": mentor.sessions_count,
                    },
                )
                for mentor in mentors
            ]

    # -- matching --------------------------------------------------------
    def calculate_match(self, student, vacancy):
        from apps.matching.engine import compute_match

        return compute_match(student, vacancy)

    def explain_candidate(self, student, vacancy) -> Explanation:
        """The "Why this candidate?" answer required by prompt §19.

        Facts and codes, not a percentage and not free prose: the employer sees
        what the score is made of, and the reasoning stays auditable.
        """
        from apps.matching.services import get_match

        with _logged(
            AIUseCase.MATCH_EXPLAIN,
            user=student,
            provider=self.provider,
            model=self.model,
            payload={"student": str(student.id), "vacancy": str(vacancy.id)},
        ):
            match = get_match(student, vacancy)
            if match is None:
                return Explanation(summary_code="no_match_data")

            if match.overall_score >= 80:
                summary = "strong_candidate"
            elif match.overall_score >= 60:
                summary = "promising_candidate"
            elif match.overall_score >= 40:
                summary = "partial_candidate"
            else:
                summary = "weak_candidate"

            return Explanation(
                summary_code=summary,
                reasons=match.explanation,
                data={
                    "overall": match.overall_score,
                    "coverage": match.coverage_score,
                    "knowledge": match.knowledge_score,
                    "verification": match.verification_score,
                    "experience": match.experience_score,
                    "matched_skills": match.matched_skills,
                    "missing_skills": match.missing_skills,
                },
            )

    # -- CV --------------------------------------------------------------
    def improve_cv(self, cv, vacancy=None) -> list[dict]:
        from apps.experience.models import Experience
        from apps.profiles.models import SkillStatus, UserSkill

        with _logged(
            AIUseCase.CV,
            user=cv.user,
            provider=self.provider,
            model=self.model,
            payload={"cv": str(cv.id), "vacancy": str(vacancy.id) if vacancy else None},
        ):
            suggestions: list[dict] = []
            user = cv.user

            if not cv.summary:
                suggestions.append({"code": "cv.add_summary", "severity": "high"})
            if len(cv.summary or "") > 600:
                suggestions.append({"code": "cv.summary_too_long", "severity": "low"})

            unverified = UserSkill.objects.filter(
                user=user, status=SkillStatus.DECLARED
            ).count()
            if unverified:
                suggestions.append(
                    {
                        "code": "cv.verify_skills",
                        "severity": "medium",
                        "data": {"count": unverified},
                    }
                )

            if not Experience.objects.filter(user=user).exists():
                suggestions.append({"code": "cv.add_experience", "severity": "high"})

            without_skills = Experience.objects.filter(
                user=user, skill_links__isnull=True
            ).count()
            if without_skills:
                suggestions.append(
                    {
                        "code": "cv.tag_experience_skills",
                        "severity": "medium",
                        "data": {"count": without_skills},
                    }
                )

            if vacancy is not None:
                match = self.calculate_match(user, vacancy)
                if match.missing_skills:
                    suggestions.append(
                        {
                            "code": "cv.missing_for_vacancy",
                            "severity": "high",
                            "data": {
                                "skills": [s["skill"] for s in match.missing_skills[:5]],
                                "vacancy": vacancy.title,
                            },
                        }
                    )
            return suggestions


class LLMAIService(RuleBasedAIService):
    """Scaffold for a hosted model.

    Inherits the rule-based behaviour so an unconfigured or failing provider
    degrades to something correct instead of to nothing. Each method would call
    the provider, run the output through `safety.check_text`, and fall back to
    `super()` on error — the wiring is intentionally left explicit rather than
    half-implemented against a provider that has not been chosen (TZ §21).
    """

    def __init__(self, config: AIProviderConfig):
        self.config = config
        self.provider = config.provider
        self.model = config.model

    def _api_key(self) -> str | None:
        import os

        if not self.config.api_key_env_name:
            return None
        return os.environ.get(self.config.api_key_env_name)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def get_ai_service() -> AIService:
    config = AIProviderConfig.objects.filter(is_active=True).first()
    if config is None or config.provider == AIProvider.RULE_BASED:
        return RuleBasedAIService()
    return LLMAIService(config)


# ---------------------------------------------------------------------------
# Persistence of recommendations
# ---------------------------------------------------------------------------
def refresh_recommendations(user, *, limit: int = 5) -> int:
    """Regenerate and store the user's recommendation set."""
    service = get_ai_service()
    produced: list[Recommendation] = []
    produced += service.recommend_courses(user, limit=limit)
    produced += service.recommend_vacancies(user, limit=limit)
    produced += service.generate_career_recommendations(user, limit=3)
    produced += service.recommend_mentors(user, limit=3)

    for item in produced:
        AIRecommendation.objects.update_or_create(
            user=user,
            type=item.type,
            ref_id=item.ref_id,
            defaults={
                "ref_type": item.ref_type,
                "title": item.title[:255],
                "score": item.score,
                "reason_code": item.reason_code,
                "reason_data": item.reason_data,
                "expires_at": timezone.now() + timezone.timedelta(days=14),
            },
        )
    return len(produced)


def get_recommendations(user, *, type: str | None = None, limit: int = 20):
    queryset = AIRecommendation.objects.filter(user=user).exclude(status="DISMISSED")
    if type:
        queryset = queryset.filter(type=type)
    if not queryset.exists():
        refresh_recommendations(user)
        queryset = AIRecommendation.objects.filter(user=user).exclude(status="DISMISSED")
        if type:
            queryset = queryset.filter(type=type)
    return queryset.order_by("-score")[:limit]
