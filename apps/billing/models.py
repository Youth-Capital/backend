"""Plans, subscriptions, payments and usage counters.

Two decisions worth stating up front.

Money is stored in **minor units as an integer** (so 149000 UZS, not 1490.00).
Float arithmetic on money accumulates error and there is no reason to invite
it; the currency's exponent lives on the row so a zero-decimal currency is
representable too.

A plan's limits live in a **JSON map keyed by feature code** rather than in a
PlanFeature table. The set of gateable features is fixed by the code that
calls `require_feature` — a row for a feature nothing checks would silently
grant nothing — so a join per feature buys nothing but latency.
"""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.enums import Role
from apps.common.models import BaseModel, TranslatableNameMixin

from .enums import (
    BillingInterval,
    PaymentStatus,
    PlanTier,
    SubscriptionStatus,
)


class Plan(TranslatableNameMixin, BaseModel):
    """A purchasable tier, scoped to the role it is sold to.

    Scoping by role is what keeps an employer from subscribing to the student
    Premium plan and inheriting limits that were never priced for them.
    """

    code = models.SlugField(max_length=64, unique=True)
    role = models.CharField(max_length=16, choices=Role.choices, db_index=True)
    tier = models.CharField(max_length=16, choices=PlanTier.choices)

    price_minor = models.PositiveIntegerField(
        default=0,
        help_text=_("Price in minor units, e.g. 149000 means 1 490.00 for a 2-exponent currency."),
    )
    currency = models.CharField(max_length=3, default="UZS")
    currency_exponent = models.PositiveSmallIntegerField(default=2)
    interval = models.CharField(
        max_length=8, choices=BillingInterval.choices, default=BillingInterval.MONTH
    )
    trial_days = models.PositiveSmallIntegerField(default=0)

    #: {feature_code: int | None}. A missing key denies the feature outright;
    #: an explicit null grants it without limit.
    limits = models.JSONField(default=dict, blank=True)

    #: Marketing bullet points, resolved per language on the client.
    highlights = models.JSONField(default=list, blank=True)

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text=_("Assigned automatically on registration for this role."),
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("role", "sort_order", "price_minor")
        constraints = [
            models.UniqueConstraint(
                fields=("role",),
                condition=models.Q(is_default=True),
                name="billing_one_default_plan_per_role",
            ),
        ]
        indexes = [models.Index(fields=("role", "is_active"))]

    def __str__(self) -> str:
        return f"{self.code} ({self.get_role_display()})"

    @property
    def price_major(self) -> float:
        return self.price_minor / (10**self.currency_exponent)

    @property
    def is_free(self) -> bool:
        return self.price_minor == 0

    def limit_for(self, feature: str) -> int | None:
        """None means unlimited; KeyError-free absence means denied."""
        return self.limits.get(feature)

    def grants(self, feature: str) -> bool:
        return feature in self.limits


class Subscription(BaseModel):
    """One row per user. History lives in Payment, not in extra subscriptions."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(
        max_length=16,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
        db_index=True,
    )

    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    trial_end = models.DateTimeField(null=True, blank=True)

    cancel_at_period_end = models.BooleanField(default=False)
    canceled_at = models.DateTimeField(null=True, blank=True)

    provider = models.CharField(max_length=32, default="manual")
    provider_ref = models.CharField(max_length=128, blank=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=("status", "current_period_end"))]

    def __str__(self) -> str:
        return f"{self.user_id} → {self.plan.code} [{self.status}]"

    @property
    def is_entitling(self) -> bool:
        """Does this subscription currently grant its plan's features?

        A period that has lapsed stops entitling even if the status column was
        never swept — the read path must not depend on a cron having run.
        """
        if self.status not in SubscriptionStatus.entitling():
            return False
        if self.current_period_end and self.current_period_end < timezone.now():
            return False
        return True

    @property
    def renews_on(self):
        return None if self.cancel_at_period_end else self.current_period_end


class FeatureUsage(BaseModel):
    """Metered consumption inside one billing period.

    Keyed by period start so a counter resets simply by a new period producing
    a new row — no scheduled job has to zero anything, and last period's usage
    stays queryable for support and invoicing.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="feature_usage"
    )
    feature = models.CharField(max_length=48, db_index=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField(null=True, blank=True)
    used = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0)])

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "feature", "period_start"),
                name="billing_usage_unique_per_period",
            ),
        ]
        indexes = [models.Index(fields=("user", "feature", "period_start"))]

    def __str__(self) -> str:
        return f"{self.user_id}/{self.feature}={self.used}"


class Payment(BaseModel):
    """An attempted or completed charge.

    Kept even when it fails: a failed payment is the evidence behind a
    PAST_DUE subscription, and deleting it would erase why access was cut.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments"
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="payments")

    amount_minor = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="UZS")
    currency_exponent = models.PositiveSmallIntegerField(default=2)

    status = models.CharField(
        max_length=16,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )
    provider = models.CharField(max_length=32, default="manual")
    provider_ref = models.CharField(max_length=128, blank=True, db_index=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("user", "-created_at"))]

    def __str__(self) -> str:
        return f"{self.provider}:{self.provider_ref or self.id} [{self.status}]"

    @property
    def amount_major(self) -> float:
        return self.amount_minor / (10**self.currency_exponent)
