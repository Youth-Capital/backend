"""Matching endpoints."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.exceptions import DomainError
from apps.common.permissions import IsAdmin, IsStudent
from apps.jobs.models import Vacancy

from ..models import MatchResult, MatchWeightProfile, ProfessionMatch
from ..services import (
    get_match,
    recompute_matches_for_student,
    recompute_profession_matches,
    refresh_stale_matches,
)


class MatchResultSerializer(serializers.ModelSerializer):
    vacancy_title = serializers.CharField(source="vacancy.title", read_only=True)
    company = serializers.CharField(
        source="vacancy.employer.display_name", read_only=True
    )

    class Meta:
        model = MatchResult
        fields = [
            "id",
            "vacancy",
            "vacancy_title",
            "company",
            "overall_score",
            "coverage_score",
            "knowledge_score",
            "verification_score",
            "experience_score",
            "education_score",
            "location_score",
            # Beside the score, never inside it. The client shows the two
            # separately because they answer different questions: can you do
            # this job, and is it the kind of job you said you want.
            "relevance_score",
            "relevance_known",
            "relevance_reasons",
            "matched_skills",
            "missing_skills",
            "explanation",
            "computed_at",
            "is_stale",
        ]
        read_only_fields = fields


class ProfessionMatchSerializer(serializers.ModelSerializer):
    profession_name = serializers.SerializerMethodField()

    class Meta:
        model = ProfessionMatch
        fields = [
            "id",
            "profession",
            "profession_name",
            "score",
            "matched_skills",
            "missing_skills",
            "breakdown",
            "computed_at",
        ]
        read_only_fields = fields

    def get_profession_name(self, match) -> str:
        return match.profession.name


class MatchWeightProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchWeightProfile
        fields = [
            "id",
            "name",
            "version",
            "is_active",
            "weights",
            "min_score_to_notify",
            "notes",
        ]
        read_only_fields = ["id"]


@extend_schema(tags=["matching"])
class MyMatchesView(APIView):
    """The student's ranked vacancies."""

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request):
        """Vacancies this person could do, filtered to the ones they want.

        The relevance filter is applied here as well as in the pool, and that
        is not redundant. The pool decides what gets *computed* from now on;
        this decides what gets *shown*, which matters for every result already
        in the table from before there was such a thing as relevance. Without
        it the feed would stay noisy until a full recompute had worked its way
        through, and a stored row for a job somebody has since decided against
        would keep resurfacing.

        `relevance_known=False` rows are kept deliberately: that flag means the
        learner has declared nothing, and filtering them out would empty the
        page for exactly the people with the least to go on.

        `?all=1` turns the filter off — for the learner who wants to see
        everything they qualify for, which is a reasonable thing to want and
        should not require guessing a query parameter's absence.
        """
        from django.db.models import Q

        from ..relevance import RELEVANT_ENOUGH

        limit = min(int(request.query_params.get("limit", 20)), 100)
        min_score = int(request.query_params.get("min_score", 0))
        unfiltered = request.query_params.get("all") in {"1", "true", "yes"}

        def feed():
            rows = MatchResult.objects.filter(
                student=request.user, overall_score__gte=min_score
            ).select_related("vacancy", "vacancy__employer")
            if not unfiltered:
                rows = rows.filter(
                    Q(relevance_known=False)
                    | Q(relevance_score__gte=RELEVANT_ENOUGH)
                )
            return rows

        matches = feed()
        if not matches.exists():
            recompute_matches_for_student(request.user, limit=50)
            matches = feed()

        return Response(
            MatchResultSerializer(
                # Relevance first, then fit. A job in the direction somebody
                # named beats a slightly better-scoring one outside it —
                # ordering by score alone would put the filter's survivors in
                # an order that ignores the reason they survived.
                matches.order_by("-relevance_score", "-overall_score")[:limit],
                many=True,
            ).data
        )

    def post(self, request):
        """Force a recompute — used after a big profile change."""
        count = recompute_matches_for_student(request.user, limit=100)
        return Response({"recomputed": count})


@extend_schema(tags=["matching"])
class VacancyMatchView(APIView):
    """Score for one specific vacancy, with the reasons."""

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, vacancy_id):
        vacancy = Vacancy.objects.filter(id=vacancy_id).first()
        if vacancy is None:
            raise DomainError("Unknown vacancy.", code="unknown_vacancy")
        match = get_match(request.user, vacancy)
        if match is None:
            return Response(None)
        return Response(MatchResultSerializer(match).data)


@extend_schema(tags=["matching"])
class ProfessionMatchView(APIView):
    """Readiness across professions — the Career Path ranking."""

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request):
        matches = ProfessionMatch.objects.filter(student=request.user).select_related(
            "profession"
        )
        if not matches.exists():
            recompute_profession_matches(request.user)
            matches = ProfessionMatch.objects.filter(
                student=request.user
            ).select_related("profession")
        return Response(
            ProfessionMatchSerializer(matches.order_by("-score"), many=True).data
        )

    def post(self, request):
        return Response({"recomputed": recompute_profession_matches(request.user)})


@extend_schema(tags=["matching"])
class MatchWeightProfileViewSet(viewsets.ModelViewSet):
    """Admin-tunable weights (prompt §20: "сделай веса конфигурируемыми")."""

    permission_classes = [IsAdmin]
    serializer_class = MatchWeightProfileSerializer
    queryset = MatchWeightProfile.objects.all().order_by("-is_active", "name")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @extend_schema(request=None, responses={200: MatchWeightProfileSerializer})
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        profile = self.get_object()
        MatchWeightProfile.objects.filter(is_active=True).exclude(pk=profile.pk).update(
            is_active=False
        )
        profile.is_active = True
        profile.save(update_fields=["is_active", "updated_at"])

        # Every stored score was computed with the old weights.
        MatchResult.objects.update(is_stale=True)

        from apps.audit.models import AuditAction, AuditSeverity
        from apps.audit.services import log_action

        log_action(
            action=AuditAction.CONFIG_CHANGE,
            obj=profile,
            actor=request.user,
            note="match weight profile activated",
            severity=AuditSeverity.WARNING,
        )
        return Response(MatchWeightProfileSerializer(profile).data)

    @extend_schema(request=None, responses={200: dict})
    @action(detail=False, methods=["post"], url_path="refresh-stale")
    def refresh_stale(self, request):
        batch = min(int(request.data.get("batch_size", 200)), 1000)
        return Response({"refreshed": refresh_stale_matches(batch_size=batch)})
