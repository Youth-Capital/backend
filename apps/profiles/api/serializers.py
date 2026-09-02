"""Profile and skill serializers."""

from rest_framework import serializers

from apps.common.serializers import TranslatedField
from apps.taxonomy.api.serializers import ProfessionListSerializer, RegionSerializer

from ..models import (
    Education,
    EmployerProfile,
    MentorProfile,
    SkillEvidence,
    StudentProfile,
    UserSkill,
)


class StudentProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    age = serializers.IntegerField(read_only=True)
    is_minor = serializers.BooleanField(read_only=True)
    region_detail = RegionSerializer(source="region", read_only=True)
    target_profession_detail = ProfessionListSerializer(
        source="target_profession", read_only=True
    )

    class Meta:
        model = StudentProfile
        fields = [
            "id",
            "youth_id",
            "first_name",
            "last_name",
            "full_name",
            "birth_date",
            "age",
            "is_minor",
            "gender",
            "avatar",
            "bio",
            "region",
            "region_detail",
            "city",
            "education_status",
            "institution",
            "study_year",
            "target_profession",
            "target_profession_detail",
            "employment_status",
            "open_to_work",
            "languages",
            "profile_completion",
            "onboarding_completed_at",
            "diagnostics_completed_at",
            "is_high_potential",
        ]
        read_only_fields = [
            "id",
            "youth_id",
            "profile_completion",
            "onboarding_completed_at",
            "diagnostics_completed_at",
            # Set by the platform, never by the user — otherwise anyone could
            # flag themselves for the accelerated track.
            "is_high_potential",
        ]


class PublicStudentCardSerializer(serializers.ModelSerializer):
    """Pseudonymised card for employer talent search.

    No name, no contacts, no avatar until the student applied to this employer
    or opted into talent search (docs/02-ARCHITECTURE.md §5).
    """

    region_name = TranslatedField("name", source="region")
    target_profession_name = TranslatedField("name", source="target_profession")

    class Meta:
        model = StudentProfile
        fields = [
            "youth_id",
            "region_name",
            "city",
            "education_status",
            "target_profession_name",
            "employment_status",
            "open_to_work",
        ]
        read_only_fields = fields


class EmployerProfileSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    is_verified = serializers.BooleanField(read_only=True)
    region_detail = RegionSerializer(source="region", read_only=True)

    class Meta:
        model = EmployerProfile
        fields = [
            "id",
            "legal_name",
            "brand_name",
            "display_name",
            "slug",
            "tax_id",
            "industry",
            "size",
            "website",
            "logo",
            "description",
            "region",
            "region_detail",
            "address",
            "contact_email",
            "contact_phone",
            "verification_status",
            "is_verified",
            "verified_at",
            "is_active",
        ]
        read_only_fields = [
            "id",
            "slug",
            "verification_status",
            "verified_at",
            "is_active",
        ]


class MentorProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    expertise_detail = serializers.SerializerMethodField()

    class Meta:
        model = MentorProfile
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "headline",
            "bio",
            "avatar",
            "expertise",
            "expertise_detail",
            "professions",
            "years_experience",
            "is_free",
            "hourly_rate",
            "currency",
            "languages",
            "availability",
            "rating_avg",
            "rating_count",
            "sessions_count",
            "accepting_students",
            "verification_status",
        ]
        read_only_fields = [
            "id",
            "rating_avg",
            "rating_count",
            "sessions_count",
            "verification_status",
        ]

    def get_expertise_detail(self, mentor) -> list[dict]:
        return [
            {"id": str(skill.id), "name": skill.name} for skill in mentor.expertise.all()
        ]


class SkillEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SkillEvidence
        fields = [
            "id",
            "source",
            "score",
            "weight",
            "ref_type",
            "ref_id",
            "issued_at",
            "note",
        ]
        read_only_fields = fields


class UserSkillSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")
    category = serializers.CharField(source="skill.category.slug", read_only=True)
    category_name = TranslatedField("name", source="skill.category")
    band = serializers.CharField(read_only=True)
    is_verified = serializers.BooleanField(read_only=True)
    knowledge_score = serializers.SerializerMethodField()

    class Meta:
        model = UserSkill
        fields = [
            "id",
            "skill",
            "skill_name",
            "category",
            "category_name",
            "proficiency",
            "confidence",
            "status",
            "band",
            "is_verified",
            "best_source",
            "last_evidence_at",
            "is_highlighted",
            "knowledge_score",
        ]
        read_only_fields = [
            "id",
            "proficiency",
            "confidence",
            "status",
            "best_source",
            "last_evidence_at",
        ]

    def get_knowledge_score(self, user_skill) -> int | None:
        cache = self.context.get("knowledge_by_skill")
        if cache is None:
            return None
        return cache.get(user_skill.skill_id)


class UserSkillDetailSerializer(UserSkillSerializer):
    evidence = SkillEvidenceSerializer(many=True, read_only=True)

    class Meta(UserSkillSerializer.Meta):
        fields = [*UserSkillSerializer.Meta.fields, "evidence"]


class DeclareSkillSerializer(serializers.Serializer):
    skill = serializers.UUIDField()
    proficiency = serializers.IntegerField(min_value=0, max_value=100)


class EducationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Education
        fields = [
            "id",
            "institution",
            "degree",
            "field_of_study",
            "start_date",
            "end_date",
            "is_current",
            "gpa",
            "language",
        ]


class OnboardingSerializer(serializers.Serializer):
    """One payload for the whole onboarding step (prompt §26)."""

    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    region = serializers.UUIDField(required=False, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, max_length=120)
    education_status = serializers.CharField(max_length=16)
    institution = serializers.CharField(required=False, allow_blank=True, max_length=200)
    target_profession = serializers.UUIDField(required=False, allow_null=True)
    employment_status = serializers.CharField(required=False, max_length=16)
    languages = serializers.ListField(child=serializers.DictField(), required=False)
    skills = DeclareSkillSerializer(many=True, required=False)
