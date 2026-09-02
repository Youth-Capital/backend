"""Entitlement: role + plan + feature + usage limit.

This is the single place that answers "may this user do this?" for anything
paid. The React side hides upgrade-gated buttons for tidiness only; every
gate here is enforced server-side, because a hidden button is not access
control (prompt §32, §34).

Resolution order for a user's plan:

    their subscription, if it is still entitling
      → otherwise the default plan for their role
        → otherwise nothing is granted

That fallback is what makes a lapsed subscription degrade to Free instead of
locking someone out of the product entirely.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

from apps.common.enums import Role

from .enums import Feature, LimitKind, SubscriptionStatus, limit_kind
from .models import FeatureUsage, Plan, Subscription


class FeatureUnavailable(APIException):
    """402 rather than 403: the action is legitimate, it just needs a plan."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_code = "feature_unavailable"
    default_detail = "This feature is not available on your current plan."

    def __init__(self, decision: "FeatureDecision"):
        self.decision = decision
        super().__init__(
            detail={
                "code": decision.reason,
                "feature": decision.feature,
                "limit": decision.limit,
                "used": decision.used,
                "plan": decision.plan_code,
            }
        )


@dataclass(frozen=True)
class FeatureDecision:
    """Why access was granted or refused, in codes the client can translate."""

    allowed: bool
    feature: str
    reason: str
    plan_code: str | None = None
    limit: int | None = None
    used: int = 0

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)

    @property
    def unlimited(self) -> bool:
        return self.limit is None and self.allowed


# --------------------------------------------------------------------------
# Concurrent counters
#
# CONCURRENT features count live rows instead of consumption. The counter is
# registered rather than imported at module scope so billing does not import
# jobs, which imports matching, which imports billing.
# --------------------------------------------------------------------------
_COUNTERS: dict[str, Callable[[object], int]] = {}


def register_counter(feature: str, counter: Callable[[object], int]) -> None:
    _COUNTERS[feature] = counter


def _count_active_vacancies(user) -> int:
    """Published *and* awaiting moderation both occupy a slot.

    Counting only published ones would let an employer on a 1-vacancy plan
    submit ten at once: each passes the check individually, and the quota is
    then breached by the moderator approving them.
    """
    from apps.common.enums import ModerationStatus
    from apps.jobs.models import Vacancy

    return Vacancy.objects.filter(
        employer__owner=user,
        status__in=(ModerationStatus.PUBLISHED, ModerationStatus.PENDING_REVIEW),
    ).count()


register_counter(Feature.ACTIVE_VACANCY, _count_active_vacancies)


# --------------------------------------------------------------------------
# Plan resolution
# --------------------------------------------------------------------------
def default_plan_for(role: str) -> Plan | None:
    return Plan.objects.filter(role=role, is_default=True, is_active=True).first()


def active_subscription(user) -> Subscription | None:
    subscription = (
        Subscription.objects.select_related("plan").filter(user=user).first()
    )
    if subscription and subscription.is_entitling:
        return subscription
    return None


def resolve_plan(user) -> Plan | None:
    subscription = active_subscription(user)
    if subscription:
        return subscription.plan
    return default_plan_for(user.role)


def period_bounds(user) -> tuple[datetime, datetime | None]:
    """The window metered usage is counted in.

    A paid subscription meters against its own billing period. Everyone else
    meters against the calendar month, so a free tier still resets rather than
    accumulating forever and silently becoming a lifetime cap.
    """
    subscription = active_subscription(user)
    if subscription and subscription.current_period_end:
        return subscription.current_period_start, subscription.current_period_end

    now = timezone.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day = calendar.monthrange(now.year, now.month)[1]
    end = start.replace(day=last_day) + timedelta(days=1)
    return start, end


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------
def usage_for(user, feature: str) -> int:
    if limit_kind(feature) == LimitKind.CONCURRENT:
        counter = _COUNTERS.get(feature)
        return counter(user) if counter else 0

    start, _end = period_bounds(user)
    row = FeatureUsage.objects.filter(
        user=user, feature=feature, period_start=start
    ).first()
    return row.used if row else 0


def billing_configured() -> bool:
    """Is there a plan catalogue at all?

    An empty catalogue means billing has not been set up on this deployment,
    which must not be the same thing as "everything is forbidden". Failing
    closed here would mean a forgotten `seed_plans` takes down courses,
    applications and candidate search — the entire product — on a platform
    where billing is an addition to something that already worked.

    Once a single plan exists the gate enforces strictly, so this cannot be
    used to dodge a limit in a configured deployment.
    """
    return Plan.objects.filter(is_active=True).exists()


def check_feature(user, feature: str) -> FeatureDecision:
    """Non-mutating: reports the decision without consuming anything."""
    if not user or not user.is_authenticated:
        return FeatureDecision(False, feature, "not_authenticated")

    # Staff bypass the paywall; they are not the ones being sold to.
    if user.role == Role.ADMIN or user.is_superuser:
        return FeatureDecision(True, feature, "admin", plan_code=None, limit=None)

    if not billing_configured():
        return FeatureDecision(True, feature, "billing_disabled", plan_code=None)

    plan = resolve_plan(user)
    if plan is None:
        # Catalogue exists but this role has no default plan — a real
        # misconfiguration, so this one does fail closed.
        return FeatureDecision(False, feature, "no_plan")

    if not plan.grants(feature):
        return FeatureDecision(False, feature, "feature_not_in_plan", plan.code)

    limit = plan.limit_for(feature)
    if limit is None:
        return FeatureDecision(True, feature, "unlimited", plan.code, None, 0)

    used = usage_for(user, feature)
    if used >= limit:
        return FeatureDecision(False, feature, "limit_reached", plan.code, limit, used)

    return FeatureDecision(True, feature, "ok", plan.code, limit, used)


def require_feature(user, feature: str) -> FeatureDecision:
    decision = check_feature(user, feature)
    if not decision.allowed:
        raise FeatureUnavailable(decision)
    return decision


@transaction.atomic
def consume(user, feature: str, amount: int = 1) -> FeatureDecision:
    """Check and increment in one transaction.

    The row is locked before the limit is re-read, otherwise two concurrent
    requests both observe `used == limit - 1` and both proceed, letting a
    100-search plan serve 101 searches.

    CONCURRENT features are not incremented here — their count is derived from
    live rows, so the caller creating the object *is* the increment.
    """
    decision = check_feature(user, feature)
    if not decision.allowed:
        raise FeatureUnavailable(decision)

    if limit_kind(feature) == LimitKind.CONCURRENT or decision.limit is None:
        return decision

    start, end = period_bounds(user)
    row, _created = FeatureUsage.objects.select_for_update().get_or_create(
        user=user,
        feature=feature,
        period_start=start,
        defaults={"period_end": end, "used": 0},
    )

    if row.used + amount > decision.limit:
        raise FeatureUnavailable(
            FeatureDecision(
                False, feature, "limit_reached", decision.plan_code, decision.limit, row.used
            )
        )

    row.used += amount
    row.save(update_fields=["used", "updated_at"])

    return FeatureDecision(
        True, feature, "ok", decision.plan_code, decision.limit, row.used
    )


def usage_summary(user) -> list[dict]:
    """Every gated feature on the user's plan, with its current consumption.

    Drives the billing page's usage meters. Features the plan does not grant
    are included with `granted=False` so the UI can show what upgrading buys
    instead of silently omitting it.
    """
    plan = resolve_plan(user)
    relevant = [
        feature
        for feature in Feature.values
        if _feature_applies_to(feature, user.role)
    ]

    summary = []
    for feature in relevant:
        granted = bool(plan and plan.grants(feature))
        limit = plan.limit_for(feature) if granted else None
        summary.append(
            {
                "feature": feature,
                "granted": granted,
                "limit": limit,
                "used": usage_for(user, feature) if granted else 0,
                "kind": limit_kind(feature),
            }
        )
    return summary


_STUDENT_FEATURES = {
    Feature.AI_CHAT,
    Feature.AI_ASSISTANT,
    Feature.SKILL_GAP_ADVANCED,
    Feature.CAREER_PATH_PERSONAL,
    Feature.CV_ANALYSIS,
    Feature.CV_EXPORT,
    Feature.COURSE_ENROLLMENT,
    Feature.JOB_APPLICATION,
}
_EMPLOYER_FEATURES = {
    Feature.AI_CHAT,
    Feature.ACTIVE_VACANCY,
    Feature.CANDIDATE_SEARCH,
    Feature.CANDIDATE_ANALYTICS,
    Feature.EMPLOYER_TEST,
    Feature.EMPLOYER_COURSE,
    Feature.TALENT_PIPELINE,
}


def _feature_applies_to(feature: str, role: str) -> bool:
    if role == Role.STUDENT:
        return feature in _STUDENT_FEATURES
    if role == Role.EMPLOYER:
        return feature in _EMPLOYER_FEATURES
    return False


# --------------------------------------------------------------------------
# Subscription lifecycle
# --------------------------------------------------------------------------
def period_end_for(plan: Plan, start: datetime) -> datetime | None:
    from .enums import BillingInterval

    if plan.interval == BillingInterval.MONTH:
        return start + timedelta(days=30)
    if plan.interval == BillingInterval.YEAR:
        return start + timedelta(days=365)
    return None


@transaction.atomic
def ensure_subscription(user) -> Subscription:
    """Every user has a subscription row, free tier included.

    Materialising the free plan rather than treating "no row" as free keeps
    the billing page, usage counters and upgrade flow reading from one shape.
    """
    existing = Subscription.objects.select_related("plan").filter(user=user).first()
    if existing:
        return existing

    plan = default_plan_for(user.role)
    if plan is None:
        raise RuntimeError(
            f"No default plan configured for role {user.role}. Run seed_plans."
        )

    now = timezone.now()
    return Subscription.objects.create(
        user=user,
        plan=plan,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=now,
        current_period_end=period_end_for(plan, now),
    )


@transaction.atomic
def activate_plan(user, plan: Plan, *, provider: str = "manual", provider_ref: str = "") -> Subscription:
    """Move a user onto a plan and start a fresh billing period.

    Usage counters are not carried over: the new period gets new rows, which
    is what makes an upgrade take effect immediately rather than the user
    still being blocked by last plan's exhausted quota.
    """
    if plan.role != user.role:
        raise ValueError(f"Plan {plan.code} is not sold to role {user.role}.")

    subscription = ensure_subscription(user)
    now = timezone.now()

    subscription.plan = plan
    subscription.status = (
        SubscriptionStatus.TRIALING if plan.trial_days else SubscriptionStatus.ACTIVE
    )
    subscription.current_period_start = now
    subscription.current_period_end = period_end_for(plan, now)
    subscription.trial_end = (
        now + timedelta(days=plan.trial_days) if plan.trial_days else None
    )
    subscription.cancel_at_period_end = False
    subscription.canceled_at = None
    subscription.provider = provider
    subscription.provider_ref = provider_ref
    subscription.save()

    return subscription


@transaction.atomic
def cancel_subscription(user, *, immediately: bool = False) -> Subscription:
    """Cancel at period end by default.

    Cutting access the moment someone clicks cancel takes away time they have
    already paid for; the default keeps the plan running until the period they
    bought actually ends.
    """
    subscription = ensure_subscription(user)
    now = timezone.now()

    subscription.canceled_at = now
    if immediately:
        subscription.status = SubscriptionStatus.CANCELED
        subscription.current_period_end = now
    else:
        subscription.cancel_at_period_end = True
    subscription.save()

    return subscription


def downgrade_expired() -> int:
    """Move lapsed subscriptions onto their role's free plan.

    `is_entitling` already refuses access the moment a period lapses, so this
    is housekeeping for reporting rather than a load-bearing security control.
    """
    now = timezone.now()
    stale = Subscription.objects.select_related("plan").filter(
        current_period_end__lt=now,
        status__in=SubscriptionStatus.entitling(),
    )

    moved = 0
    for subscription in stale:
        free = default_plan_for(subscription.user.role)
        if free is None or subscription.plan_id == free.id:
            subscription.status = SubscriptionStatus.EXPIRED
            subscription.save(update_fields=["status", "updated_at"])
            continue
        subscription.plan = free
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.current_period_start = now
        subscription.current_period_end = period_end_for(free, now)
        subscription.cancel_at_period_end = False
        subscription.save()
        moved += 1
    return moved
