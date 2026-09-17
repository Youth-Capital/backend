"""Billing endpoints.

Plans are public because pricing is marketing. Everything else is scoped to
the requesting user — there is deliberately no way to read another account's
subscription, usage or payments through this API.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..enums import Feature
from ..models import Plan
from ..payments import PaymentService
from ..services import (
    check_feature,
    default_plan_for,
    ensure_subscription,
    usage_summary,
)
from .serializers import (
    CancelRequestSerializer,
    CheckoutRequestSerializer,
    CheckoutResponseSerializer,
    ConfirmRequestSerializer,
    FeatureCheckSerializer,
    PaymentSerializer,
    PlanSerializer,
    SubscriptionSerializer,
    UsageEntrySerializer,
)


class PlanListView(APIView):
    """Public pricing. Optionally filtered to one role."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(responses=PlanSerializer(many=True))
    def get(self, request):
        plans = Plan.objects.filter(is_active=True)
        role = request.query_params.get("role")
        if role:
            plans = plans.filter(role=role.upper())
        return Response(PlanSerializer(plans, many=True, context={"request": request}).data)


class MySubscriptionView(APIView):
    """The caller's subscription, materialising the free tier on first read."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            200: SubscriptionSerializer,
            404: OpenApiResponse(description="Nothing is sold to this role."),
        }
    )
    def get(self, request):
        # Admins are not customers, so no plan is defined for them.
        # Without this guard `ensure_subscription` raises and the endpoint 500s
        # for a perfectly valid, authenticated user.
        if default_plan_for(request.user.role) is None:
            return Response(
                {"detail": "No plans are offered for this role.", "code": "not_sold"},
                status=status.HTTP_404_NOT_FOUND,
            )

        subscription = ensure_subscription(request.user)
        return Response(
            SubscriptionSerializer(subscription, context={"request": request}).data
        )


class UsageView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=UsageEntrySerializer(many=True))
    def get(self, request):
        return Response(UsageEntrySerializer(usage_summary(request.user), many=True).data)


class FeatureCheckView(APIView):
    """Lets the client grey out what it would be refused anyway.

    Advisory only. The same check runs again server-side when the action is
    actually attempted — this endpoint exists for UX, not for security.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=FeatureCheckSerializer,
        description="Advisory entitlement check for one feature.",
    )
    def get(self, request, feature: str):
        if feature not in Feature.values:
            return Response(
                {"detail": "Unknown feature."}, status=status.HTTP_404_NOT_FOUND
            )
        decision = check_feature(request.user, feature)
        return Response(
            {
                "feature": decision.feature,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "limit": decision.limit,
                "used": decision.used,
                "remaining": decision.remaining,
                "plan": decision.plan_code,
            }
        )


class CheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=CheckoutRequestSerializer,
        responses={
            200: CheckoutResponseSerializer,
            400: OpenApiResponse(description="Plan not on sale or wrong role."),
        },
    )
    def post(self, request):
        payload = CheckoutRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            plan = Plan.objects.get(code=payload.validated_data["plan_code"], is_active=True)
        except Plan.DoesNotExist:
            return Response(
                {"detail": "Unknown plan."}, status=status.HTTP_404_NOT_FOUND
            )

        service = PaymentService()
        try:
            payment, session = service.create_checkout_session(
                request.user,
                plan,
                success_url=payload.validated_data.get("success_url", ""),
                cancel_url=payload.validated_data.get("cancel_url", ""),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "reference": session.reference,
                "provider": session.provider,
                "redirect_url": session.redirect_url,
                "payment": PaymentSerializer(payment).data,
            }
        )


class ConfirmView(APIView):
    """Settle a checkout. Idempotent; re-reads the outcome from the provider."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=ConfirmRequestSerializer, responses=PaymentSerializer)
    def post(self, request):
        payload = ConfirmRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        reference = payload.validated_data["reference"]

        from ..models import Payment

        # Scoped to the caller: knowing a reference must not be enough to settle
        # somebody else's payment.
        if not Payment.objects.filter(
            provider_ref=reference, user=request.user
        ).exists():
            return Response(
                {"detail": "Unknown payment reference."},
                status=status.HTTP_404_NOT_FOUND,
            )

        payment = PaymentService().confirm(reference)
        return Response(PaymentSerializer(payment).data)


class CancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=CancelRequestSerializer, responses=SubscriptionSerializer)
    def post(self, request):
        payload = CancelRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        if default_plan_for(request.user.role) is None:
            return Response(
                {"detail": "No plans are offered for this role.", "code": "not_sold"},
                status=status.HTTP_404_NOT_FOUND,
            )

        subscription = PaymentService().cancel(
            request.user, immediately=payload.validated_data["immediately"]
        )
        return Response(
            SubscriptionSerializer(subscription, context={"request": request}).data
        )


class PaymentHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=PaymentSerializer(many=True))
    def get(self, request):
        payments = PaymentService().history(request.user)[:100]
        return Response(PaymentSerializer(payments, many=True).data)
