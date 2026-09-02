"""PaymentService — the only thing views call to move money.

Views never touch a provider directly. That indirection is what keeps the
checkout/confirm flow identical whether settlement happens in-process or at
Stripe (prompt §31).
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action

from .enums import PaymentStatus
from .models import Payment, Plan, Subscription
from .providers import CheckoutSession, get_provider
from .services import activate_plan, cancel_subscription, ensure_subscription


class PaymentService:
    """Checkout, settlement and history.

    Every method is safe to call twice: `confirm` is idempotent because a
    payment that already succeeded returns its existing subscription rather
    than activating the plan a second time and restarting the billing period.
    """

    def __init__(self, provider=None):
        self.provider = provider or get_provider()

    # -- checkout ---------------------------------------------------------
    @transaction.atomic
    def create_checkout_session(
        self, user, plan: Plan, *, success_url: str = "", cancel_url: str = ""
    ) -> tuple[Payment, CheckoutSession]:
        if plan.role != user.role:
            raise ValueError(f"Plan {plan.code} is not sold to role {user.role}.")
        if not plan.is_active:
            raise ValueError(f"Plan {plan.code} is not on sale.")

        session = self.provider.create_checkout(
            user=user, plan=plan, success_url=success_url, cancel_url=cancel_url
        )

        payment = Payment.objects.create(
            user=user,
            plan=plan,
            subscription=ensure_subscription(user),
            amount_minor=plan.price_minor,
            currency=plan.currency,
            currency_exponent=plan.currency_exponent,
            status=PaymentStatus.PENDING,
            provider=session.provider,
            provider_ref=session.reference,
        )
        return payment, session

    # -- settlement -------------------------------------------------------
    @transaction.atomic
    def confirm(self, reference: str) -> Payment:
        """Re-read settlement from the provider and apply it.

        The client tells us *which* payment to check, never *whether* it
        succeeded — otherwise upgrading would be a matter of posting
        `{"status": "paid"}`.
        """
        payment = (
            Payment.objects.select_for_update()
            .select_related("plan", "user")
            .get(provider_ref=reference)
        )

        if payment.status == PaymentStatus.SUCCEEDED:
            return payment  # idempotent replay

        outcome = self.provider.verify(reference)

        if not outcome.succeeded:
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = outcome.failure_reason[:255]
            payment.save(update_fields=["status", "failure_reason", "updated_at"])
            return payment

        if outcome.amount_minor != payment.amount_minor:
            # The provider settled a different amount than we recorded. Refusing
            # here is what stops a tampered or stale session from buying a plan
            # at another plan's price.
            payment.status = PaymentStatus.FAILED
            payment.failure_reason = "amount_mismatch"
            payment.save(update_fields=["status", "failure_reason", "updated_at"])
            return payment

        payment.status = PaymentStatus.SUCCEEDED
        payment.paid_at = timezone.now()
        payment.save(update_fields=["status", "paid_at", "updated_at"])

        subscription = activate_plan(
            payment.user,
            payment.plan,
            provider=payment.provider,
            provider_ref=payment.provider_ref,
        )
        payment.subscription = subscription
        payment.save(update_fields=["subscription", "updated_at"])

        log_action(
            action="BILLING_PLAN_ACTIVATED",
            actor=payment.user,
            obj=subscription,
            after={
                "plan": payment.plan.code,
                "payment": str(payment.id),
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
                "provider": payment.provider,
            },
        )
        return payment

    # -- lifecycle --------------------------------------------------------
    def create_subscription(self, user, plan: Plan) -> Subscription:
        """Activate without a charge. Free plans and administrative grants only."""
        if not plan.is_free:
            raise ValueError(
                f"{plan.code} is a paid plan; go through create_checkout_session."
            )
        return activate_plan(user, plan, provider="manual", provider_ref="")

    def cancel(self, user, *, immediately: bool = False) -> Subscription:
        subscription = cancel_subscription(user, immediately=immediately)
        try:
            self.provider.cancel(subscription)
        except NotImplementedError:
            pass  # provider holds no mandate to revoke
        log_action(
            action="BILLING_SUBSCRIPTION_CANCELED",
            actor=user,
            obj=subscription,
            after={"plan": subscription.plan.code, "immediately": immediately},
        )
        return subscription

    def history(self, user):
        return (
            Payment.objects.filter(user=user)
            .select_related("plan")
            .order_by("-created_at")
        )
