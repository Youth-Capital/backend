"""CV, portfolio and Youth Passport endpoints (prompt §10)."""

from django.utils.text import slugify
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.common.permissions import IsOwnerOrAdmin, IsStudent
from apps.common.exceptions import DomainError

from ..models import CVDocument, PortfolioItem, PublicProfile
from ..rating import refresh_cv_rating
from ..services import build_cv_payload


class CVDocumentSerializer(serializers.ModelSerializer):
    enabled_sections = serializers.ListField(read_only=True)

    class Meta:
        model = CVDocument
        fields = [
            "id",
            "title",
            "language",
            "template",
            "headline",
            "summary",
            "sections_config",
            "enabled_sections",
            "target_profession",
            "is_primary",
            "quality_score",
            "quality_computed_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "quality_score",
            "quality_computed_at",
            "updated_at",
        ]


class PortfolioItemSerializer(serializers.ModelSerializer):
    skill_names = serializers.SerializerMethodField()
    # Uploaded in, addressed out. The stored file has no public address at
    # all, so what a client gets back is a link to the download view that
    # checks who is asking.
    file_url = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()

    class Meta:
        model = PortfolioItem
        fields = [
            "id",
            "title",
            "description",
            "type",
            "url",
            "file",
            "file_url",
            "cover",
            "cover_url",
            "skills",
            "skill_names",
            "experience",
            "order",
            "is_public",
        ]
        read_only_fields = ["id", "file_url", "cover_url"]
        extra_kwargs = {
            "file": {"write_only": True, "required": False},
            "cover": {"write_only": True, "required": False},
        }

    def get_skill_names(self, item) -> list[str]:
        return [skill.name for skill in item.skills.all()]

    def get_file_url(self, item) -> str | None:
        from apps.common.api.files import file_url

        return file_url("portfolio-item", item, self.context.get("request"))

    def get_cover_url(self, item) -> str | None:
        from apps.common.api.files import file_url

        return file_url("portfolio-cover", item, self.context.get("request"))


class PublicProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicProfile
        fields = ["id", "public_slug", "is_public", "visible_blocks", "views_count"]
        read_only_fields = ["id", "views_count"]


@extend_schema(tags=["cv"])
class CVViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    serializer_class = CVDocumentSerializer

    #: Read by ScopedRateThrottle, which is attached to the drafting action
    #: only — see `get_throttles`. A model-backed call belongs on the same
    #: budget as the rest of them rather than on the ordinary per-user rate.
    throttle_scope = "ai"

    def get_permissions(self):
        if self.action == "draft":
            # A CV is a student's document, and the draft is written from a
            # student's record. There is nothing for an employer account to
            # draft, so this says so rather than returning an empty one.
            return [IsAuthenticated(), IsStudent()]
        return super().get_permissions()

    def get_throttles(self):
        if self.action == "draft":
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        return CVDocument.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        is_first = not CVDocument.objects.filter(user=self.request.user).exists()
        cv = serializer.save(user=self.request.user, is_primary=is_first)
        refresh_cv_rating(cv)

        from apps.analytics.services import track

        track(self.request.user, "cv_created", {"cv_id": str(cv.id)})

    def perform_update(self, serializer):
        """Only one CV can be primary — clear the others rather than letting
        the unique constraint reject the save."""
        if serializer.validated_data.get("is_primary"):
            CVDocument.objects.filter(user=self.request.user, is_primary=True).exclude(
                pk=serializer.instance.pk
            ).update(is_primary=False)
        # The rating is a function of the document plus the profile behind it,
        # so it is recomputed on every edit rather than left to drift.
        refresh_cv_rating(serializer.save())

    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """Render-ready CV assembled live from the profile.

        The document stores which sections to show; the content is always read
        fresh, so a CV can never drift out of date.
        """
        return Response(build_cv_payload(self.get_object()))

    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["get"])
    def rating(self, request, pk=None):
        """CV quality rating — the same number the employer sees.

        Recomputed on read rather than served from the column: the score
        depends on skills and experience that change outside this document, and
        a student who just passed a test should see the effect immediately.
        """
        return Response(refresh_cv_rating(self.get_object()))

    @extend_schema(responses={200: dict})
    @extend_schema(
        request=None,
        responses={200: dict},
        description=(
            "A first draft of the headline and summary, written from the "
            "student's own skills, experience and finished courses. Nothing is "
            "saved: the draft comes back as text for the form."
        ),
    )
    @action(detail=False, methods=["post"])
    def draft(self, request):
        """Have the assistant write the opening, instead of writing it yourself.

        A draft, never a document. The student sees the sentences in the same
        form they would have typed into, and nothing reaches the database until
        they press save — which is the difference between a tool and something
        filing papers under their name.
        """
        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import consume
        from apps.taxonomy.models import Profession

        from ..composer import draft_cv

        # Metered like every other model-backed capability: a draft is a round
        # trip once a provider is configured, and the quota is what keeps one
        # account from spending the platform's budget.
        consume(request.user, BillingFeature.CV_ANALYSIS)

        profession = None
        requested = request.data.get("profession")
        if requested:
            profession = Profession.objects.filter(id=requested).first()
        if profession is None:
            profile = getattr(request.user, "student_profile", None)
            profession = getattr(profile, "target_profession", None)

        return Response(
            draft_cv(
                request.user,
                profession=profession,
                language=request.data.get("language") or request.user.preferred_language,
            )
        )

    @action(detail=True, methods=["get"])
    def suggestions(self, request, pk=None):
        """AI suggestions for improving this CV (prompt §13, CV AI)."""
        from apps.ai.services import get_ai_service
        from apps.jobs.models import Vacancy

        vacancy = None
        vacancy_id = request.query_params.get("vacancy")
        if vacancy_id:
            vacancy = Vacancy.objects.filter(id=vacancy_id).first()

        return Response(
            {"suggestions": get_ai_service().improve_cv(self.get_object(), vacancy)}
        )


@extend_schema(tags=["cv"])
class PortfolioViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    serializer_class = PortfolioItemSerializer

    def get_queryset(self):
        return (
            PortfolioItem.objects.filter(user=self.request.user)
            .prefetch_related("skills")
            .order_by("order", "-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(tags=["cv"])
class MyPublicProfileView(APIView):
    """Youth Passport settings — off until the user turns it on."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile = self._get_or_create(request.user)
        return Response(PublicProfileSerializer(profile).data)

    def patch(self, request):
        profile = self._get_or_create(request.user)
        serializer = PublicProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def _get_or_create(self, user) -> PublicProfile:
        profile = getattr(user, "public_profile", None)
        if profile is not None:
            return profile

        student = getattr(user, "student_profile", None)
        base = slugify(getattr(student, "full_name", "") or "") or "youth"
        slug = f"{base}-{str(user.id)[:8]}"
        return PublicProfile.objects.create(
            user=user,
            public_slug=slug,
            is_public=False,
            visible_blocks={
                "skills": True,
                "experience": True,
                "portfolio": True,
                "certificates": True,
                "contacts": False,
            },
        )


@extend_schema(tags=["cv"])
class PublicPassportView(APIView):
    """Unauthenticated read of a Youth Passport the owner made public."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request, slug):
        profile = PublicProfile.objects.filter(
            public_slug=slug, is_public=True
        ).select_related("user").first()
        if profile is None:
            raise DomainError("Profile not found.", code="not_found")

        PublicProfile.objects.filter(pk=profile.pk).update(
            views_count=profile.views_count + 1
        )
        from ..services import build_passport_payload

        return Response(build_passport_payload(profile))
