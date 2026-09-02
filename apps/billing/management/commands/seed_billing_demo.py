"""Put demo accounts on varied plans with believable usage.

Without this every demo account sits on Free with zero consumption, and the
billing page demonstrates nothing: no meter moves, no history, no upgrade
state to look at. It also backfills accounts created before billing existed,
which the post-save signal cannot reach.

Idempotent — safe to re-run.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.common.enums import Role

from ...enums import Feature, PaymentStatus
from ...models import FeatureUsage, Payment, Plan
from ...services import activate_plan, ensure_subscription, period_bounds

#: email → (plan code, metered usage to fabricate)
ASSIGNMENTS = {
    "it@demo.uz": (
        "employer-pro",
        {Feature.CANDIDATE_SEARCH: 42},
    ),
    # Deliberately left over quota: this account already has 3 published
    # vacancies on a 1-vacancy plan. Introducing a limit must never retroactively
    # unpublish somebody's existing content — the gate blocks the *next* submission
    # and leaves what is already live alone. Worth having on screen.
    "fintech@demo.uz": (
        "employer-free",
        {Feature.CANDIDATE_SEARCH: 8},
    ),
    "cyber@demo.uz": (
        "employer-enterprise",
        {Feature.CANDIDATE_SEARCH: 137},
    ),
}

#: The first student found gets Premium so the student side has a paid example.
STUDENT_PREMIUM_USAGE = {
    Feature.AI_ASSISTANT: 23,
    Feature.CV_ANALYSIS: 4,
}


class Command(BaseCommand):
    help = "Assign demo accounts to plans and fabricate believable usage."

    @transaction.atomic
    def handle(self, *args, **options):
        if not Plan.objects.exists():
            self.stdout.write(
                self.style.ERROR("No plans found. Run seed_plans first.")
            )
            return

        # Backfill: accounts created before the billing app existed never fired
        # the signal, so they have no subscription row at all.
        backfilled = 0
        for user in User.objects.filter(role__in=(Role.STUDENT, Role.EMPLOYER)):
            if not hasattr(user, "subscription"):
                ensure_subscription(user)
                backfilled += 1

        assigned = 0
        for email, (plan_code, usage) in ASSIGNMENTS.items():
            user = User.objects.filter(email=email).first()
            plan = Plan.objects.filter(code=plan_code).first()
            if user is None or plan is None:
                continue

            activate_plan(user, plan)
            self._record_usage(user, usage)
            if not plan.is_free:
                self._record_payment(user, plan)
            assigned += 1

        # One premium student, chosen deterministically so re-running does not
        # keep moving the upgrade to a different person.
        student = (
            User.objects.filter(role=Role.STUDENT).order_by("email").first()
        )
        premium = Plan.objects.filter(code="student-premium").first()
        if student and premium:
            activate_plan(student, premium)
            self._record_usage(student, STUDENT_PREMIUM_USAGE)
            self._record_payment(student, premium)
            assigned += 1
            self.stdout.write(f"  Premium student: {student.email}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Billing demo: {backfilled} backfilled, {assigned} assigned."
            )
        )

    def _record_usage(self, user, usage: dict) -> None:
        """Set counters to a fixed value rather than incrementing.

        `update_or_create` keeps this idempotent; using `consume()` here would
        push the numbers higher on every re-run and eventually trip the limit.
        """
        start, end = period_bounds(user)
        for feature, used in usage.items():
            FeatureUsage.objects.update_or_create(
                user=user,
                feature=feature,
                period_start=start,
                defaults={"period_end": end, "used": used},
            )

    def _record_payment(self, user, plan: Plan) -> None:
        subscription = user.subscription
        Payment.objects.update_or_create(
            user=user,
            provider_ref=f"demo_{plan.code}_{user.pk}",
            defaults={
                "plan": plan,
                "subscription": subscription,
                "amount_minor": plan.price_minor,
                "currency": plan.currency,
                "currency_exponent": plan.currency_exponent,
                "status": PaymentStatus.SUCCEEDED,
                "provider": "manual",
                "paid_at": timezone.now() - timedelta(days=3),
            },
        )
