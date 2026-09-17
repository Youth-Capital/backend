"""Event tracking and dashboard aggregation."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.common.context import client_ip_var

from .models import AnalyticsEvent, DailyMetric

logger = logging.getLogger(__name__)


def track(user, name: str, properties: dict | None = None, *, session_id: str = "") -> None:
    """Record one analytics event. Never raises."""
    try:
        ip = client_ip_var.get()
        AnalyticsEvent.objects.create(
            user=user if getattr(user, "pk", None) else None,
            name=name,
            properties=properties or {},
            session_id=session_id[:64],
            ip_hash=hashlib.sha256(ip.encode()).hexdigest() if ip else "",
        )
    except Exception:  # pragma: no cover
        logger.exception("Failed to track event %s", name)


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------
def get_platform_overview() -> dict:
    """Admin dashboard headline numbers (prompt §21)."""
    from apps.accounts.models import User
    from apps.common.enums import ModerationStatus, Role
    from apps.jobs.models import Application, Placement, Vacancy
    from apps.learning.models import Course
    from apps.matching.models import MatchResult

    now = timezone.now()
    active_since = now - timedelta(days=30)

    return {
        "total_users": User.objects.count(),
        "students": User.objects.filter(role=Role.STUDENT).count(),
        "employers": User.objects.filter(role=Role.EMPLOYER).count(),
        "active_users_30d": User.objects.filter(last_login__gte=active_since).count(),
        "courses": Course.objects.count(),
        "courses_published": Course.objects.filter(
            status=ModerationStatus.PUBLISHED
        ).count(),
        "courses_pending": Course.objects.filter(
            status=ModerationStatus.PENDING_REVIEW
        ).count(),
        "vacancies": Vacancy.objects.count(),
        "vacancies_published": Vacancy.objects.filter(
            status=ModerationStatus.PUBLISHED
        ).count(),
        "vacancies_pending": Vacancy.objects.filter(
            status=ModerationStatus.PENDING_REVIEW
        ).count(),
        "applications": Application.objects.count(),
        "placements": Placement.objects.count(),
        "strong_matches": MatchResult.objects.filter(overall_score__gte=70).count(),
    }


def get_funnel() -> list[dict]:
    """TZ §14: registration -> diagnostics -> IDP -> learning -> job -> outcome."""
    from apps.accounts.models import User
    from apps.common.enums import Role
    from apps.idp.models import DevelopmentPlan
    from apps.jobs.models import Application, Placement
    from apps.learning.models import Enrollment, EnrollmentStatus
    from apps.profiles.models import StudentProfile

    total = User.objects.filter(role=Role.STUDENT).count()
    steps = [
        ("registered", total),
        (
            "onboarded",
            StudentProfile.objects.filter(onboarding_completed_at__isnull=False).count(),
        ),
        (
            "diagnosed",
            StudentProfile.objects.filter(
                diagnostics_completed_at__isnull=False
            ).count(),
        ),
        ("plan_created", DevelopmentPlan.objects.values("user").distinct().count()),
        ("learning", Enrollment.objects.values("user").distinct().count()),
        (
            "course_completed",
            Enrollment.objects.filter(status=EnrollmentStatus.COMPLETED)
            .values("user")
            .distinct()
            .count(),
        ),
        ("applied", Application.objects.values("student").distinct().count()),
        ("placed", Placement.objects.values("student").distinct().count()),
    ]

    return [
        {
            "step": name,
            "count": count,
            "rate": round(100 * count / total) if total else 0,
        }
        for name, count in steps
    ]


def get_risk_list(*, inactive_days: int = 14, limit: int = 50) -> list[dict]:
    """TZ §14 "Risk list" — who is drifting away from their plan.

    Absent from the prompt entirely, but it is the panel that makes the
    manager's dashboard actionable rather than merely descriptive.
    """
    from apps.idp.models import Task, TaskStatus
    from apps.profiles.models import StudentProfile

    cutoff = timezone.now() - timedelta(days=inactive_days)
    today = timezone.localdate()

    profiles = (
        StudentProfile.objects.filter(last_activity_at__lt=cutoff)
        .select_related("user", "region", "target_profession")
        .order_by("last_activity_at")[:limit]
    )

    rows = []
    for profile in profiles:
        overdue = Task.objects.filter(
            user_id=profile.user_id,
            status__in=[TaskStatus.TODO, TaskStatus.IN_PROGRESS],
            due_date__lt=today,
        ).count()
        idle_days = (timezone.now() - profile.last_activity_at).days
        rows.append(
            {
                "user_id": str(profile.user_id),
                "youth_id": profile.youth_id,
                "name": profile.full_name,
                "region": profile.region.name if profile.region else None,
                "target_profession": profile.target_profession.name
                if profile.target_profession
                else None,
                "idle_days": idle_days,
                "overdue_tasks": overdue,
                "profile_completion": profile.profile_completion,
                "risk_score": min(100, idle_days * 2 + overdue * 10),
            }
        )
    return sorted(rows, key=lambda r: r["risk_score"], reverse=True)


def get_cohort_breakdown(dimension: str = "region") -> list[dict]:
    """Cohort analytics by an allowed segment (TZ §14)."""
    from apps.profiles.models import StudentProfile

    allowed = {
        "region": "region__name_uz",
        "education_status": "education_status",
        "employment_status": "employment_status",
        "target_profession": "target_profession__name_uz",
        "gender": "gender",
    }
    field = allowed.get(dimension)
    if field is None:
        return []

    rows = (
        StudentProfile.objects.values(field)
        .annotate(
            count=Count("id"),
            avg_completion=Avg("profile_completion"),
        )
        .order_by("-count")
    )
    return [
        {
            "segment": row[field] or "—",
            "count": row["count"],
            "avg_profile_completion": round(row["avg_completion"] or 0),
        }
        for row in rows
    ]


def get_skill_demand(limit: int = 15) -> list[dict]:
    """Which skills employers ask for versus how many students hold them."""
    from apps.jobs.models import VacancySkill
    from apps.profiles.models import SkillStatus, UserSkill
    from apps.taxonomy.models import Skill

    demand = (
        VacancySkill.objects.values("skill_id")
        .annotate(demand=Count("id"))
        .order_by("-demand")[:limit]
    )
    skill_ids = [row["skill_id"] for row in demand]
    skills = {s.id: s for s in Skill.objects.filter(id__in=skill_ids)}

    supply = {
        row["skill_id"]: row
        for row in UserSkill.objects.filter(skill_id__in=skill_ids)
        .values("skill_id")
        .annotate(
            supply=Count("id"),
            verified=Count("id", filter=Q(status=SkillStatus.VERIFIED)),
        )
    }

    rows = []
    for row in demand:
        skill = skills.get(row["skill_id"])
        if skill is None:
            continue
        stats = supply.get(row["skill_id"], {})
        rows.append(
            {
                "skill": skill.name,
                "skill_id": str(skill.id),
                "demand": row["demand"],
                "supply": stats.get("supply", 0),
                "verified_supply": stats.get("verified", 0),
                "gap": row["demand"] - stats.get("verified", 0),
            }
        )
    return rows


def get_outcome_metrics() -> dict:
    """The KPIs the TZ actually judges the programme on (§15)."""
    from apps.jobs.models import Application, ApplicationStatus, Placement

    placements = Placement.objects.all()
    applications = Application.objects.all()
    total_applications = applications.count()
    hired = applications.filter(status=ApplicationStatus.ACCEPTED).count()

    return {
        "applications": total_applications,
        "hired": hired,
        "conversion_rate": round(100 * hired / total_applications)
        if total_applications
        else 0,
        "placements": placements.count(),
        "retention_30": placements.filter(retention_30=True).count(),
        "retention_90": placements.filter(retention_90=True).count(),
        "retention_180": placements.filter(retention_180=True).count(),
        "interviews": applications.filter(
            status__in=[ApplicationStatus.INTERVIEW, ApplicationStatus.OFFER]
        ).count(),
    }


def snapshot_daily_metrics() -> int:
    """Persist today's headline numbers. Run from a scheduled command."""
    today = timezone.localdate()
    overview = get_platform_overview()
    outcomes = get_outcome_metrics()

    written = 0
    for key, value in {**overview, **outcomes}.items():
        if isinstance(value, (int, float)):
            DailyMetric.objects.update_or_create(
                date=today,
                key=key,
                dimension_key="",
                dimension_value="",
                defaults={"value": value},
            )
            written += 1
    return written
