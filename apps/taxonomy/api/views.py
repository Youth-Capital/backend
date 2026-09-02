"""Taxonomy endpoints — readable by any signed-in user, writable by admins."""

from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.pagination import LargePagination
from apps.common.permissions import IsAuthenticatedReadOnlyOrAdmin

from ..models import (
    CapitalDimension,
    Profession,
    ProfessionSkill,
    Region,
    Skill,
    SkillCategory,
    SkillDimension,
)
from .serializers import (
    CapitalDimensionSerializer,
    ProfessionDetailSerializer,
    ProfessionListSerializer,
    ProfessionSkillSerializer,
    ProfessionWriteSerializer,
    RegionSerializer,
    SkillCategorySerializer,
    SkillDimensionSerializer,
    SkillSerializer,
    SkillWriteSerializer,
)


@extend_schema(tags=["taxonomy"])
class RegionViewSet(viewsets.ModelViewSet):
    queryset = Region.objects.filter(is_active=True).order_by("code")
    serializer_class = RegionSerializer
    permission_classes = [IsAuthenticatedReadOnlyOrAdmin]
    pagination_class = LargePagination
    search_fields = ["name_uz", "name_ru", "name_en", "code"]


@extend_schema(tags=["taxonomy"])
class SkillCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = SkillCategorySerializer
    permission_classes = [IsAuthenticatedReadOnlyOrAdmin]
    pagination_class = LargePagination
    filterset_fields = ["parent", "is_active"]
    search_fields = ["name_uz", "name_ru", "name_en", "slug"]

    def get_queryset(self):
        return (
            SkillCategory.objects.select_related("parent")
            .annotate(skill_count=Count("skills", filter=Q(skills__is_active=True)))
            .order_by("order", "name_uz")
        )

    @extend_schema(responses={200: SkillCategorySerializer(many=True)})
    @action(detail=False, methods=["get"])
    def tree(self, request):
        """Whole tree in one call — the skill picker needs it all at once."""
        categories = self.get_queryset()
        serialized = {
            str(c.id): {**SkillCategorySerializer(c).data, "children": []}
            for c in categories
        }
        roots = []
        for category in categories:
            node = serialized[str(category.id)]
            if category.parent_id and str(category.parent_id) in serialized:
                serialized[str(category.parent_id)]["children"].append(node)
            else:
                roots.append(node)
        return Response(roots)


@extend_schema(tags=["taxonomy"])
class SkillViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticatedReadOnlyOrAdmin]
    pagination_class = LargePagination
    filterset_fields = ["category", "is_active"]
    search_fields = ["name_uz", "name_ru", "name_en", "slug", "aliases"]
    ordering_fields = ["name_uz", "created_at"]

    def get_queryset(self):
        return Skill.objects.select_related("category").order_by("name_uz")

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return SkillWriteSerializer
        return SkillSerializer

    @extend_schema(responses={200: SkillSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def search(self, request):
        """Alias-aware lookup for the skill autocomplete."""
        from apps.profiles.services import search_skills

        results = search_skills(request.query_params.get("q", ""), limit=25)
        return Response(SkillSerializer(results, many=True).data)


@extend_schema(tags=["taxonomy"])
class CapitalDimensionViewSet(viewsets.ModelViewSet):
    queryset = CapitalDimension.objects.all().order_by("order")
    serializer_class = CapitalDimensionSerializer
    permission_classes = [IsAuthenticatedReadOnlyOrAdmin]
    pagination_class = LargePagination


@extend_schema(tags=["taxonomy"])
class SkillDimensionViewSet(viewsets.ModelViewSet):
    queryset = SkillDimension.objects.select_related("skill", "dimension")
    serializer_class = SkillDimensionSerializer
    permission_classes = [IsAuthenticatedReadOnlyOrAdmin]
    pagination_class = LargePagination
    filterset_fields = ["skill", "dimension"]


@extend_schema(tags=["taxonomy"])
class ProfessionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticatedReadOnlyOrAdmin]
    filterset_fields = ["category", "demand_level", "is_active"]
    search_fields = ["name_uz", "name_ru", "name_en", "slug"]
    ordering_fields = ["name_uz", "demand_level", "median_salary"]

    def get_queryset(self):
        queryset = Profession.objects.select_related("category")
        if self.action == "retrieve":
            return queryset.prefetch_related("skill_links__skill__category")
        return queryset.annotate(
            required_count=Count(
                "skill_links", filter=Q(skill_links__requirement="REQUIRED")
            )
        ).order_by("name_uz")

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return ProfessionWriteSerializer
        if self.action == "retrieve":
            return ProfessionDetailSerializer
        return ProfessionListSerializer

    @extend_schema(request=ProfessionSkillSerializer, responses={201: ProfessionSkillSerializer})
    @action(detail=True, methods=["post"], url_path="skills")
    def add_skill(self, request, pk=None):
        profession = self.get_object()
        serializer = ProfessionSkillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link, _created = ProfessionSkill.objects.update_or_create(
            profession=profession,
            skill=serializer.validated_data["skill"],
            defaults={
                "requirement": serializer.validated_data.get("requirement", "REQUIRED"),
                "min_proficiency": serializer.validated_data.get("min_proficiency", 50),
                "weight": serializer.validated_data.get("weight", 1),
                "order": serializer.validated_data.get("order", 0),
            },
        )
        return Response(ProfessionSkillSerializer(link).data, status=201)

    @extend_schema(responses={204: None})
    @action(detail=True, methods=["delete"], url_path=r"skills/(?P<skill_id>[^/.]+)")
    def remove_skill(self, request, pk=None, skill_id=None):
        profession = self.get_object()
        ProfessionSkill.objects.filter(profession=profession, skill_id=skill_id).delete()
        return Response(status=204)

    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["get"], url_path="career-path")
    def career_path(self, request, pk=None):
        """Current -> missing -> recommended courses -> vacancies (prompt §5)."""
        from apps.common.enums import ModerationStatus
        from apps.jobs.models import Vacancy
        from apps.learning.models import Course
        from apps.profiles.services import get_skill_gap

        profession = self.get_object()
        gap = get_skill_gap(request.user, profession)

        missing_ids = [e["skill_id"] for e in gap["missing_skills"] + gap["partial_skills"]]
        courses = (
            Course.objects.filter(
                status=ModerationStatus.PUBLISHED, skill_links__skill_id__in=missing_ids
            )
            .distinct()
            .order_by("-rating_avg")[:6]
        )
        vacancies = (
            Vacancy.objects.filter(
                status=ModerationStatus.PUBLISHED, profession=profession
            )
            .select_related("employer")
            .order_by("-published_at")[:6]
        )

        return Response(
            {
                **gap,
                "recommended_courses": [
                    {
                        "id": str(c.id),
                        "title": c.title,
                        "level": c.level,
                        "duration_minutes": c.duration_minutes,
                        "rating": float(c.rating_avg),
                    }
                    for c in courses
                ],
                "related_vacancies": [
                    {
                        "id": str(v.id),
                        "title": v.title,
                        "company": v.employer.display_name,
                        "employment_type": v.employment_type,
                    }
                    for v in vacancies
                ],
            }
        )
