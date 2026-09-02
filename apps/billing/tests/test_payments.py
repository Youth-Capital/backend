"""Checkout, settlement and cancellation.

The important assertions here are the ones that stop a user upgrading
themselves for free: settlement is re-read from the provider, and an amount
that does not match the recorded payment is refused.
"""

import pytest
from django.test import override_settings

from apps.billing.enums import (
    BillingInterval,
    Feature,
    PaymentStatus,
    PlanTier,
    SubscriptionStatus,
)
from apps.billing.models import Payment, Plan
from apps.billing.payments import PaymentService
from apps.billing.providers import PaymentOutcome, reset_provider_cache
from apps.common.enums import Role

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    reset_provider_cache()
    yield
    reset_provider_cache()


@pytest.fixture
def free_plan():
    return Plan.objects.create(
        code="p-free",
        role=Role.STUDENT,
        tier=PlanTier.FREE,
        name_uz="Bepul",
        price_minor=0,
        interval=BillingInterval.NONE,
        is_default=True,
        limits={Feature.JOB_APPLICATION: 2},
    )


@pytest.fixture
def paid_plan():
    return Plan.objects.create(
        code="p-premium",
        role=Role.STUDENT,
        tier=PlanTier.PREMIUM,
        name_uz="Premium",
        price_minor=49_000,
        currency="UZS",
        currency_exponent=0,
        interval=BillingInterval.MONTH,
        limits={Feature.AI_ASSISTANT: 5},
    )


@pytest.fixture
def student(django_user_model):
    return django_user_model.objects.create_user(
        email="pay@example.com", password="Str0ng!passw0rd", role=Role.STUDENT
    )


@override_settings(BILLING_MANUAL_AUTO_CONFIRM=True)
def test_checkout_then_confirm_activates_the_plan(free_plan, paid_plan, student):
    service = PaymentService()
    payment, session = service.create_checkout_session(student, paid_plan)

    assert payment.status == PaymentStatus.PENDING
    assert payment.amount_minor == 49_000

    settled = service.confirm(session.reference)

    assert settled.status == PaymentStatus.SUCCEEDED
    assert settled.paid_at is not None
    student.refresh_from_db()
    assert student.subscription.plan.code == paid_plan.code
    assert student.subscription.status == SubscriptionStatus.ACTIVE


@override_settings(BILLING_MANUAL_AUTO_CONFIRM=True)
def test_confirm_is_idempotent(free_plan, paid_plan, student):
    service = PaymentService()
    _payment, session = service.create_checkout_session(student, paid_plan)

    first = service.confirm(session.reference)
    period_start = first.user.subscription.current_period_start

    second = service.confirm(session.reference)

    assert second.id == first.id
    # Replaying must not restart the billing period the user paid for.
    second.user.refresh_from_db()
    assert second.user.subscription.current_period_start == period_start
    assert Payment.objects.filter(status=PaymentStatus.SUCCEEDED).count() == 1


@override_settings(BILLING_MANUAL_AUTO_CONFIRM=False)
def test_unsettled_payment_does_not_upgrade(free_plan, paid_plan, student):
    service = PaymentService()
    _payment, session = service.create_checkout_session(student, paid_plan)

    settled = service.confirm(session.reference)

    assert settled.status == PaymentStatus.FAILED
    assert settled.failure_reason == "awaiting_manual_confirmation"
    student.refresh_from_db()
    assert student.subscription.plan.code == free_plan.code


def test_amount_mismatch_is_refused(free_plan, paid_plan, student):
    """A provider settling a different amount must not buy the plan."""

    class LyingProvider:
        name = "liar"

        def create_checkout(self, *, user, plan, success_url="", cancel_url=""):
            from apps.billing.providers import CheckoutSession

            return CheckoutSession(reference="ref_lie", provider=self.name)

        def verify(self, reference):
            return PaymentOutcome(reference, True, amount_minor=1, currency="UZS")

        def cancel(self, subscription):
            return None

    service = PaymentService(provider=LyingProvider())
    _payment, session = service.create_checkout_session(student, paid_plan)

    settled = service.confirm(session.reference)

    assert settled.status == PaymentStatus.FAILED
    assert settled.failure_reason == "amount_mismatch"
    student.refresh_from_db()
    assert student.subscription.plan.code == free_plan.code


def test_paid_plan_cannot_be_granted_without_payment(free_plan, paid_plan, student):
    with pytest.raises(ValueError):
        PaymentService().create_subscription(student, paid_plan)


@override_settings(BILLING_MANUAL_AUTO_CONFIRM=True)
def test_cancel_keeps_access_until_period_end(free_plan, paid_plan, student):
    service = PaymentService()
    _payment, session = service.create_checkout_session(student, paid_plan)
    service.confirm(session.reference)

    subscription = service.cancel(student)

    assert subscription.cancel_at_period_end is True
    assert subscription.canceled_at is not None
    assert subscription.renews_on is None
    # Paid-for time is not taken away.
    assert subscription.is_entitling is True


@override_settings(BILLING_MANUAL_AUTO_CONFIRM=True)
def test_cancel_immediately_revokes(free_plan, paid_plan, student):
    service = PaymentService()
    _payment, session = service.create_checkout_session(student, paid_plan)
    service.confirm(session.reference)

    subscription = service.cancel(student, immediately=True)

    assert subscription.status == SubscriptionStatus.CANCELED
    assert subscription.is_entitling is False
