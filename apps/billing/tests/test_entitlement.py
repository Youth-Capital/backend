"""The entitlement gate.

These cover the cases that actually cost money if wrong: quota being
enforced at all, a lapsed subscription degrading instead of locking out, and
the two limit kinds behaving differently.
"""

import pytest
from django.utils import timezone
from datetime import timedelta

from apps.billing.enums import (
    BillingInterval,
    Feature,
    PlanTier,
    SubscriptionStatus,
)
from apps.billing.models import FeatureUsage, Plan, Subscription
from apps.billing.services import (
    FeatureUnavailable,
    activate_plan,
    check_feature,
    consume,
    ensure_subscription,
    resolve_plan,
    usage_summary,
)
from apps.common.enums import Role

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    return Plan.objects.create(
        code="t-free",
        role=Role.STUDENT,
        tier=PlanTier.FREE,
        name_uz="Bepul",
        price_minor=0,
        interval=BillingInterval.NONE,
        is_default=True,
        limits={Feature.JOB_APPLICATION: 2},
    )


@pytest.fixture
def premium_plan():
    return Plan.objects.create(
        code="t-premium",
        role=Role.STUDENT,
        tier=PlanTier.PREMIUM,
        name_uz="Premium",
        price_minor=49_000,
        currency_exponent=0,
        interval=BillingInterval.MONTH,
        limits={Feature.JOB_APPLICATION: None, Feature.AI_ASSISTANT: 5},
    )


@pytest.fixture
def student(django_user_model):
    return django_user_model.objects.create_user(
        email="gate@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )


def test_feature_absent_from_plan_is_denied(free_plan, student):
    decision = check_feature(student, Feature.AI_ASSISTANT)
    assert not decision.allowed
    assert decision.reason == "feature_not_in_plan"


def test_limit_is_enforced_after_exhaustion(free_plan, student):
    consume(student, Feature.JOB_APPLICATION)
    consume(student, Feature.JOB_APPLICATION)

    with pytest.raises(FeatureUnavailable) as caught:
        consume(student, Feature.JOB_APPLICATION)

    assert caught.value.decision.reason == "limit_reached"
    assert caught.value.status_code == 402


def test_null_limit_means_unlimited(premium_plan, free_plan, student):
    activate_plan(student, premium_plan)
    for _ in range(25):
        consume(student, Feature.JOB_APPLICATION)

    decision = check_feature(student, Feature.JOB_APPLICATION)
    assert decision.allowed and decision.unlimited
    # An unlimited feature must not accumulate counter rows.
    assert not FeatureUsage.objects.filter(
        user=student, feature=Feature.JOB_APPLICATION
    ).exists()


def test_upgrade_resets_the_counter(free_plan, premium_plan, student):
    consume(student, Feature.JOB_APPLICATION)
    consume(student, Feature.JOB_APPLICATION)
    with pytest.raises(FeatureUnavailable):
        consume(student, Feature.JOB_APPLICATION)

    activate_plan(student, premium_plan)

    # New billing period → new counter row, so the upgrade takes effect now
    # rather than after the old period would have expired.
    assert check_feature(student, Feature.AI_ASSISTANT).allowed
    assert consume(student, Feature.AI_ASSISTANT).used == 1


def test_lapsed_subscription_falls_back_to_free_not_lockout(
    free_plan, premium_plan, student
):
    subscription = activate_plan(student, premium_plan)
    subscription.current_period_end = timezone.now() - timedelta(days=1)
    subscription.save(update_fields=["current_period_end"])

    # Expired premium must not entitle premium features...
    assert not check_feature(student, Feature.AI_ASSISTANT).allowed
    # ...but the free plan's own allowance still works.
    assert check_feature(student, Feature.JOB_APPLICATION).allowed
    assert resolve_plan(student).code == free_plan.code


def test_past_due_still_entitles(free_plan, premium_plan, student):
    """A failed card starts dunning; it does not instantly revoke access."""
    subscription = activate_plan(student, premium_plan)
    subscription.status = SubscriptionStatus.PAST_DUE
    subscription.save(update_fields=["status"])

    assert check_feature(student, Feature.AI_ASSISTANT).allowed


def test_admin_bypasses_the_paywall(free_plan, django_user_model):
    admin = django_user_model.objects.create_user(
        email="root@example.com", password="Str0ng!passw0rd", role=Role.ADMIN
    )
    assert check_feature(admin, Feature.AI_ASSISTANT).allowed


def test_plan_is_scoped_to_role(free_plan, student):
    employer_plan = Plan.objects.create(
        code="t-employer",
        role=Role.EMPLOYER,
        tier=PlanTier.PRO,
        name_uz="Pro",
        limits={Feature.CANDIDATE_SEARCH: 10},
    )
    with pytest.raises(ValueError):
        activate_plan(student, employer_plan)


def test_usage_summary_lists_ungranted_features(free_plan, student):
    rows = {row["feature"]: row for row in usage_summary(student)}

    assert rows[Feature.JOB_APPLICATION]["granted"] is True
    assert rows[Feature.JOB_APPLICATION]["limit"] == 2
    # Shown but not granted, so the UI can advertise what upgrading buys.
    assert rows[Feature.AI_ASSISTANT]["granted"] is False
    # Employer features never appear on a student's panel.
    assert Feature.CANDIDATE_SEARCH not in rows


def test_ensure_subscription_is_idempotent(free_plan, student):
    first = ensure_subscription(student)
    second = ensure_subscription(student)
    assert first.id == second.id
    assert Subscription.objects.filter(user=student).count() == 1


def test_empty_catalogue_does_not_lock_the_product(student):
    """No plans configured means billing is off, not that everything is denied.

    Regression guard: gating the product before `seed_plans` has run would
    otherwise 402 every course enrolment, application and candidate search on
    a fresh deployment.
    """
    assert not Plan.objects.exists()

    decision = check_feature(student, Feature.AI_ASSISTANT)

    assert decision.allowed
    assert decision.reason == "billing_disabled"


def test_catalogue_without_a_default_for_the_role_fails_closed(student):
    """A configured catalogue missing this role's free plan is a real bug."""
    Plan.objects.create(
        code="t-employer-only",
        role=Role.EMPLOYER,
        tier=PlanTier.PRO,
        name_uz="Pro",
        limits={Feature.CANDIDATE_SEARCH: 5},
    )

    decision = check_feature(student, Feature.JOB_APPLICATION)

    assert not decision.allowed
    assert decision.reason == "no_plan"
