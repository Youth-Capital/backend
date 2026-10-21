"""Paid plans switched off — the launch default.

The rest of this suite runs with BILLING_ENABLED on, because it tests the
paywall itself. These pin what "off" means: nothing is limited, nothing is
priced, nothing can be bought, and the SPA is told so.
"""

import pytest
from django.test import override_settings

from apps.billing.enums import BillingInterval, Feature, PlanTier
from apps.billing.models import FeatureUsage, Plan
from apps.billing.services import check_feature, consume
from apps.common.enums import Role

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("tight_free_plan")]


@pytest.fixture
def tight_free_plan():
    """A catalogue that would refuse the third application, were it enforced."""
    return Plan.objects.create(
        code="t-free-tight",
        role=Role.STUDENT,
        tier=PlanTier.FREE,
        name_uz="Bepul",
        price_minor=0,
        interval=BillingInterval.NONE,
        is_default=True,
        limits={Feature.JOB_APPLICATION: 2},
    )


@override_settings(BILLING_ENABLED=False)
def test_limits_do_not_apply(student):
    for _ in range(5):
        decision = consume(student, Feature.JOB_APPLICATION)
        assert decision.allowed

    assert check_feature(student, Feature.JOB_APPLICATION).reason == "billing_disabled"
    # Nothing counted either, so switching billing on later starts from zero.
    assert not FeatureUsage.objects.filter(user=student).exists()


@override_settings(BILLING_ENABLED=True)
def test_the_same_catalogue_enforces_once_switched_on(student):
    consume(student, Feature.JOB_APPLICATION)
    consume(student, Feature.JOB_APPLICATION)

    assert not check_feature(student, Feature.JOB_APPLICATION).allowed


@override_settings(BILLING_ENABLED=False)
def test_no_plans_are_listed(api):
    response = api.get("/api/v1/billing/plans/?role=STUDENT")

    assert response.status_code == 200
    assert response.data == []


@override_settings(BILLING_ENABLED=False)
def test_nothing_can_be_bought(auth, student):
    response = auth(student).post(
        "/api/v1/billing/checkout/", {"plan_code": "t-free-tight"}, format="json"
    )

    assert response.status_code == 404
    assert response.data["code"] == "billing_disabled"


@override_settings(BILLING_ENABLED=False)
def test_the_spa_is_told(auth, student):
    response = auth(student).get("/api/v1/auth/me/")

    assert response.data["billing_enabled"] is False
