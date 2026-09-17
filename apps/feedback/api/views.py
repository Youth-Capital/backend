"""Platform feedback endpoints.

Two audiences, two sets of rules. A participant may write their own review and
read their own invitation. An admin may read everything, moderate it, run
campaigns and see the aggregate — and the queryset is what enforces that split,
not the UI.
"""

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.enums import ModerationStatus, Role
from apps.common.exceptions import DomainError
from apps.common.permissions import IsAdmin

from ..models import (
    CampaignStatus,
    PlatformReview,
    ReviewCampaign,
    ReviewRequest,
    ReviewTrigger,
)
from ..services import (
    dismiss_request,
    launch_campaign,
    open_request_for,
    review_stats,
    submit_review,
)


class ReviewRequestSerializer(serializers.ModelSerializer):
    campaign_title = serializers.CharField(
        source="campaign.title", read_only=True, default=""
    )
    campaign_message = serializers.CharField(
        source="campaign.message", read_only=True, default=""
    )

    class Meta:
        model = ReviewRequest
        fields = [
            "id",
            "trigger",
            "ref_type",
            "ref_id",
            "campaign",
            "campaign_title",
            "campaign_message",
            "notified_at",
            "responded_at",
            "dismissed_at",
        ]
        read_only_fields = fields


class PlatformReviewSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()

    class Meta:
        model = PlatformReview
        fields = [
            "id",
            "author",
            "role_at_review",
            "trigger",
            "rating",
            "nps_score",
            "pros",
            "cons",
            "suggestion",
            "status",
            "is_anonymous",
            "admin_response",
            "created_at",
        ]
        read_only_fields = fields

    def get_author(self, review) -> dict:
        """Who wrote it — or that they chose not to say.

        The role travels either way: "an employer said this" is what makes the
        sentence useful, and it is not identifying on its own.
        """
        if review.is_anonymous and not self.context.get("reveal_anonymous"):
            return {"anonymous": True, "role": review.role_at_review}
        profile = getattr(review.user, "student_profile", None)
        employer = getattr(review.user, "employer_profile", None)
        return {
            "anonymous": review.is_anonymous,
            "role": review.role_at_review,
            "user_id": str(review.user_id),
            "name": (
                getattr(profile, "full_name", None)
                or getattr(employer, "display_name", None)
                or review.user.email
            ),
        }


class SubmitReviewSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    nps_score = serializers.IntegerField(
        min_value=0, max_value=10, required=False, allow_null=True
    )
    pros = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    cons = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    suggestion = serializers.CharField(
        required=False, allow_blank=True, max_length=2000
    )
    is_anonymous = serializers.BooleanField(required=False, default=False)
    request = serializers.UUIDField(required=False, allow_null=True)


class ReviewCampaignSerializer(serializers.ModelSerializer):
    response_rate = serializers.IntegerField(read_only=True)

    class Meta:
        model = ReviewCampaign
        fields = [
            "id",
            "title",
            "message",
            "audience_roles",
            "min_account_age_days",
            "status",
            "launched_at",
            "closes_at",
            "invited_count",
            "responded_count",
            "response_rate",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "launched_at",
            "invited_count",
            "responded_count",
            "response_rate",
            "created_at",
        ]

    def validate_audience_roles(self, value):
        allowed = {Role.STUDENT, Role.EMPLOYER}
        unknown = set(value) - allowed
        if unknown:
            raise serializers.ValidationError(
                f"Unknown roles: {', '.join(sorted(unknown))}"
            )
        return value


@extend_schema(tags=["feedback"])
class MyReviewView(APIView):
    """The participant's own side: am I being asked, and here is my answer."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        pending = open_request_for(request.user)
        mine = PlatformReview.objects.filter(user=request.user).order_by("-created_at")
        return Response(
            {
                "pending_request": (
                    ReviewRequestSerializer(pending).data if pending else None
                ),
                "reviews": PlatformReviewSerializer(
                    mine, many=True, context={"reveal_anonymous": True}
                ).data,
            }
        )

    @extend_schema(request=SubmitReviewSerializer, responses={201: PlatformReviewSerializer})
    def post(self, request):
        serializer = SubmitReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        invitation = None
        if data.get("request"):
            invitation = ReviewRequest.objects.filter(
                id=data["request"], user=request.user
            ).first()
            if invitation is None:
                raise DomainError("Unknown invitation.", code="not_found")

        review = submit_review(
            request.user,
            rating=data["rating"],
            pros=data.get("pros", ""),
            cons=data.get("cons", ""),
            suggestion=data.get("suggestion", ""),
            nps_score=data.get("nps_score"),
            is_anonymous=data.get("is_anonymous", False),
            request=invitation,
        )
        return Response(
            PlatformReviewSerializer(
                review, context={"reveal_anonymous": True}
            ).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["feedback"])
class DismissRequestView(APIView):
    """"Not now" — honoured for a period, then the person may be asked again."""

    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        invitation = ReviewRequest.objects.filter(
            id=request_id, user=request.user
        ).first()
        if invitation is None:
            raise DomainError("Unknown invitation.", code="not_found")
        return Response(ReviewRequestSerializer(dismiss_request(invitation)).data)


@extend_schema(tags=["feedback"])
class PublicReviewsView(APIView):
    """Moderated, published reviews — the wall anyone signed in may read."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 20)), 100)
        queryset = (
            PlatformReview.objects.filter(status=ModerationStatus.PUBLISHED)
            .select_related("user", "user__student_profile", "user__employer_profile")
            .order_by("-created_at")
        )
        role = request.query_params.get("role")
        if role:
            queryset = queryset.filter(role_at_review=role)
        return Response(
            {
                "results": PlatformReviewSerializer(queryset[:limit], many=True).data,
                "summary": review_stats(),
            }
        )


@extend_schema(tags=["feedback"])
class ReviewAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """Everything that came back, and what to do about it."""

    permission_classes = [IsAdmin]
    serializer_class = PlatformReviewSerializer
    filterset_fields = ["status", "role_at_review", "rating", "trigger", "campaign"]

    def get_queryset(self):
        return PlatformReview.objects.select_related(
            "user", "user__student_profile", "user__employer_profile", "campaign"
        ).order_by("-created_at")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        # An admin moderating a review needs to know who wrote it; the
        # anonymity promise is about other participants, not about the
        # moderator who has to act on abuse.
        context["reveal_anonymous"] = True
        return context

    @extend_schema(request=None, responses={200: dict})
    @action(detail=False, methods=["get"])
    def summary(self, request):
        role = request.query_params.get("role")
        return Response(review_stats(role=role))

    @extend_schema(request=None, responses={200: PlatformReviewSerializer})
    @action(detail=True, methods=["post"])
    def moderate(self, request, pk=None):
        review = self.get_object()
        approve = bool(request.data.get("approve", True))
        review.status = (
            ModerationStatus.PUBLISHED if approve else ModerationStatus.REJECTED
        )
        review.moderation_note = str(request.data.get("note", ""))[:2000]
        review.moderated_by = request.user
        review.save(
            update_fields=[
                "status",
                "moderation_note",
                "moderated_by",
                "updated_at",
            ]
        )

        from apps.audit.services import log_moderation

        log_moderation(
            review, decision=review.status, actor=request.user, note=review.moderation_note
        )
        return Response(
            self.get_serializer(review).data
        )

    @extend_schema(request=None, responses={200: PlatformReviewSerializer})
    @action(detail=True, methods=["post"], url_path="respond")
    def respond(self, request, pk=None):
        """Answer the author. Closing the loop is what makes people answer the
        next campaign."""
        review = self.get_object()
        text = str(request.data.get("text", "")).strip()
        if not text:
            raise DomainError("A response cannot be empty.", code="empty_response")

        review.admin_response = text[:2000]
        review.responded_by = request.user
        review.save(
            update_fields=["admin_response", "responded_by", "updated_at"]
        )

        from apps.notifications.services import notify

        notify(
            user=review.user,
            type="SYSTEM",
            title_key="notifications.review.answered.title",
            body_key="notifications.review.answered.body",
            payload={"response": review.admin_response[:280]},
            ref_type="PlatformReview",
            ref_id=review.id,
            action_url="/feedback/review",
        )
        return Response(self.get_serializer(review).data)


@extend_schema(tags=["feedback"])
class ReviewCampaignViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdmin]
    serializer_class = ReviewCampaignSerializer
    queryset = ReviewCampaign.objects.all().order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(request=None, responses={200: dict})
    @action(detail=True, methods=["post"])
    def launch(self, request, pk=None):
        campaign = self.get_object()
        invited = launch_campaign(campaign, actor=request.user)
        return Response(
            {
                "invited": invited,
                "campaign": ReviewCampaignSerializer(campaign).data,
            }
        )

    @extend_schema(request=None, responses={200: ReviewCampaignSerializer})
    @action(detail=True, methods=["post"])
    def finish(self, request, pk=None):
        campaign = self.get_object()
        campaign.status = CampaignStatus.FINISHED
        campaign.closes_at = campaign.closes_at or timezone.now()
        campaign.save(update_fields=["status", "closes_at", "updated_at"])
        return Response(ReviewCampaignSerializer(campaign).data)

    @extend_schema(request=None, responses={200: dict})
    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        campaign = self.get_object()
        reviews = PlatformReview.objects.filter(campaign=campaign).select_related(
            "user", "user__student_profile", "user__employer_profile"
        )
        return Response(
            {
                "campaign": ReviewCampaignSerializer(campaign).data,
                "reviews": PlatformReviewSerializer(
                    reviews, many=True, context={"reveal_anonymous": True}
                ).data,
                "summary": review_stats(),
                "triggers": {
                    trigger: reviews.filter(trigger=trigger).count()
                    for trigger, _label in ReviewTrigger.choices
                },
            }
        )
