"""Billing serializers.

Money crosses the wire in **both** forms: `price_minor` for arithmetic and
`price_display` for humans. Sending only a formatted string would force the
client to parse currency back into numbers; sending only minor units would
push locale-aware formatting into React for no reason.
"""

from rest_framework import serializers

from apps.common.serializers import TranslatedField

from ..enums import Feature
from ..models import Payment, Plan, Subscription


def _format_money(minor: int, currency: str, exponent: int) -> str:
    major = minor / (10**exponent)
    if exponent == 0:
        return f"{int(major):,}".replace(",", " ") + f" {currency}"
    return f"{major:,.{exponent}f}".replace(",", " ") + f" {currency}"


class PlanSerializer(serializers.ModelSerializer):
    name = TranslatedField("name")
    description = TranslatedField("description")
    price_display = serializers.SerializerMethodField()
    is_free = serializers.BooleanField(read_only=True)

    class Meta:
        model = Plan
        fields = (
            "id",
            "code",
            "role",
            "tier",
            "name",
            "description",
            "price_minor",
            "price_display",
            "currency",
            "currency_exponent",
            "interval",
            "trial_days",
            "limits",
            "highlights",
            "is_free",
            "is_default",
            "sort_order",
        )

    def get_price_display(self, obj: Plan) -> str:
        return _format_money(obj.price_minor, obj.currency, obj.currency_exponent)


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)
    is_entitling = serializers.BooleanField(read_only=True)
    renews_on = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Subscription
        fields = (
            "id",
            "plan",
            "status",
            "current_period_start",
            "current_period_end",
            "trial_end",
            "cancel_at_period_end",
            "canceled_at",
            "renews_on",
            "is_entitling",
            "provider",
        )


class PaymentSerializer(serializers.ModelSerializer):
    plan_code = serializers.CharField(source="plan.code", read_only=True)
    plan_name = serializers.SerializerMethodField()
    amount_display = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = (
            "id",
            "plan_code",
            "plan_name",
            "amount_minor",
            "amount_display",
            "currency",
            "status",
            "provider",
            "failure_reason",
            "paid_at",
            "created_at",
        )

    def get_plan_name(self, obj: Payment) -> str:
        return obj.plan.name

    def get_amount_display(self, obj: Payment) -> str:
        return _format_money(obj.amount_minor, obj.currency, obj.currency_exponent)


class UsageEntrySerializer(serializers.Serializer):
    """One row of the usage panel. `limit: null` on a granted feature means unlimited."""

    feature = serializers.ChoiceField(choices=Feature.choices)
    granted = serializers.BooleanField()
    limit = serializers.IntegerField(allow_null=True)
    used = serializers.IntegerField()
    kind = serializers.CharField()


class CheckoutRequestSerializer(serializers.Serializer):
    plan_code = serializers.SlugField()
    success_url = serializers.URLField(required=False, allow_blank=True, default="")
    cancel_url = serializers.URLField(required=False, allow_blank=True, default="")


class CheckoutResponseSerializer(serializers.Serializer):
    reference = serializers.CharField()
    provider = serializers.CharField()
    redirect_url = serializers.URLField(allow_null=True)
    payment = PaymentSerializer()


class ConfirmRequestSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=128)


class CancelRequestSerializer(serializers.Serializer):
    immediately = serializers.BooleanField(default=False)


class FeatureCheckSerializer(serializers.Serializer):
    feature = serializers.ChoiceField(choices=Feature.choices)
    allowed = serializers.BooleanField()
    reason = serializers.CharField()
    limit = serializers.IntegerField(allow_null=True)
    used = serializers.IntegerField()
    remaining = serializers.IntegerField(allow_null=True)
    plan = serializers.CharField(allow_null=True)
