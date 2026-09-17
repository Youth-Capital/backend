"""Billing endpoints over HTTP.

The admin cases are regressions: `ensure_subscription` raises for a role with
no plan, so an unguarded endpoint returned 500 to a perfectly valid user.
"""

import pytest
from rest_framework.test import APIClient

from apps.billing.enums import BillingInterval, Feature, PlanTier
from apps.billing.models import Plan
from apps.common.enums import Role

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalogue():
    Plan.objects.create(
        code="api-student-free",
        role=Role.STUDENT,
        tier=PlanTier.FREE,
        name_uz="Bepul",
        price_minor=0,
        interval=BillingInterval.NONE,
        is_default=True,
        limits={Feature.JOB_APPLICATION: 5},
    )
    Plan.objects.create(
        code="api-employer-free",
        role=Role.EMPLOYER,
        tier=PlanTier.FREE,
        name_uz="Bepul",
        price_minor=0,
        interval=BillingInterval.NONE,
        is_default=True,
        limits={Feature.ACTIVE_VACANCY: 1},
    )


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_plans_are_public(catalogue):
    response = APIClient().get("/api/v1/billing/plans/?role=STUDENT")

    assert response.status_code == 200
    codes = {plan["code"] for plan in response.json()}
    assert codes == {"api-student-free"}
    # Employer pricing must not leak into a student-scoped request.
    assert "api-employer-free" not in codes


def test_subscription_materialises_the_free_tier(catalogue, django_user_model):
    student = django_user_model.objects.create_user(
        email="s@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )

    response = _client(student).get("/api/v1/billing/subscription/")

    assert response.status_code == 200
    assert response.json()["plan"]["code"] == "api-student-free"


def test_subscription_is_404_for_a_role_nothing_is_sold_to(
    catalogue, django_user_model
):
    staff = django_user_model.objects.create_user(
        email="m@example.com", password="Str0ng!passw0rd", role=Role.ADMIN
    )

    response = _client(staff).get("/api/v1/billing/subscription/")

    assert response.status_code == 404
    assert response.json()["code"] == "not_sold"


def test_cancel_is_404_for_a_role_nothing_is_sold_to(catalogue, django_user_model):
    staff = django_user_model.objects.create_user(
        email="m2@example.com", password="Str0ng!passw0rd", role=Role.ADMIN
    )

    response = _client(staff).post("/api/v1/billing/cancel/", {"immediately": False})

    assert response.status_code == 404


def test_usage_is_empty_rather_than_broken_for_admins(catalogue, django_user_model):
    staff = django_user_model.objects.create_user(
        email="m3@example.com", password="Str0ng!passw0rd", role=Role.ADMIN
    )

    response = _client(staff).get("/api/v1/billing/usage/")

    assert response.status_code == 200
    assert response.json() == []


def test_confirm_rejects_another_users_reference(catalogue, django_user_model):
    """Knowing a payment reference must not be enough to settle it."""
    from apps.billing.models import Payment

    owner = django_user_model.objects.create_user(
        email="owner@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    attacker = django_user_model.objects.create_user(
        email="attacker@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )
    plan = Plan.objects.get(code="api-student-free")
    Payment.objects.create(
        user=owner, plan=plan, amount_minor=0, currency="UZS", provider_ref="ref_owned"
    )

    response = _client(attacker).post(
        "/api/v1/billing/confirm/", {"reference": "ref_owned"}
    )

    assert response.status_code == 404


def test_feature_check_reports_the_reason(catalogue, django_user_model):
    student = django_user_model.objects.create_user(
        email="fc@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )

    response = _client(student).get(f"/api/v1/billing/features/{Feature.AI_ASSISTANT}/")

    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is False
    assert body["reason"] == "feature_not_in_plan"
