"""Experience endpoints (prompt §9)."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.recompute import schedule_recompute
from apps.common.serializers import TranslatedField
from apps.taxonomy.models import Skill

from ..models import Experience, ExperienceSkill, ProjectAsset


class ProjectAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectAsset
        fields = ["id", "type", "file", "url", "caption", "order"]


class ExperienceSkillSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")

    class Meta:
        model = ExperienceSkill
        fields = ["id", "skill", "skill_name"]


class ExperienceSerializer(serializers.ModelSerializer):
    duration_months = serializers.IntegerField(read_only=True)
    counts_as_tenure = serializers.BooleanField(read_only=True)
    skills = ExperienceSkillSerializer(source="skill_links", many=True, read_only=True)
    assets = ProjectAssetSerializer(many=True, read_only=True)
    skill_ids = serializers.ListField(
        child=serializers.UUIDField(), write_only=True, required=False
    )

    class Meta:
        model = Experience
        fields = [
            "id",
            "type",
            "title",
            "organization",
            "description",
            "start_date",
            "end_date",
            "is_current",
            "location",
            "url",
            "verification_status",
            "duration_months",
            "counts_as_tenure",
            "skills",
            "skill_ids",
            "assets",
        ]
        read_only_fields = ["id", "verification_status"]

    def validate(self, attrs):
        start = attrs.get("start_date")
        end = attrs.get("end_date")
        if start and end and end < start:
            raise serializers.ValidationError(
                {"end_date": "End date cannot be before the start date."}
            )
        return attrs


@extend_schema(tags=["experience"])
class ExperienceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ExperienceSerializer
    filterset_fields = ["type", "is_current"]

    def get_queryset(self):
        return (
            Experience.objects.filter(user=self.request.user)
            .prefetch_related("skill_links__skill", "assets")
            .order_by("-is_current", "-start_date")
        )

    def perform_create(self, serializer):
        skill_ids = serializer.validated_data.pop("skill_ids", [])
        experience = serializer.save(user=self.request.user)
        self._sync_skills(experience, skill_ids)

        from apps.analytics.services import track

        track(self.request.user, "experience_added", {"type": experience.type})
        schedule_recompute(self.request.user, reason="experience_added")

    def perform_update(self, serializer):
        skill_ids = serializer.validated_data.pop("skill_ids", None)
        experience = serializer.save()
        if skill_ids is not None:
            self._sync_skills(experience, skill_ids)
        schedule_recompute(self.request.user, reason="experience_updated")

    def perform_destroy(self, instance):
        user = instance.user
        instance.delete()
        schedule_recompute(user, reason="experience_deleted")

    def _sync_skills(self, experience: Experience, skill_ids) -> None:
        """Replace the tag set; the signal turns each into skill evidence."""
        wanted = {str(s) for s in (skill_ids or [])}
        existing = {
            str(link.skill_id): link for link in experience.skill_links.all()
        }

        for skill_id, link in existing.items():
            if skill_id not in wanted:
                link.delete()

        for skill in Skill.objects.filter(id__in=wanted - set(existing)):
            ExperienceSkill.objects.get_or_create(experience=experience, skill=skill)

    @extend_schema(request=ProjectAssetSerializer, responses={201: ProjectAssetSerializer})
    @action(detail=True, methods=["post"], url_path="assets")
    def add_asset(self, request, pk=None):
        experience = self.get_object()
        serializer = ProjectAssetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(experience=experience)
        return Response(serializer.data, status=201)
