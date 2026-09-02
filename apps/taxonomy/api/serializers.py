"""Taxonomy serializers."""

from rest_framework import serializers

from apps.common.serializers import TranslatableModelSerializer, TranslatedField

from ..models import (
    CapitalDimension,
    Profession,
    ProfessionSkill,
    Region,
    Skill,
    SkillCategory,
    SkillDimension,
)


class RegionSerializer(TranslatableModelSerializer):
    class Meta:
        model = Region
        fields = ["id", "code", "name", "description", "parent", "is_active"]


class SkillCategorySerializer(TranslatableModelSerializer):
    path = serializers.CharField(read_only=True)
    skill_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = SkillCategory
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "parent",
            "icon",
            "order",
            "path",
            "is_active",
            "skill_count",
        ]


class SkillSerializer(TranslatableModelSerializer):
    category_name = TranslatedField("name", source="category")

    class Meta:
        model = Skill
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "category",
            "category_name",
            "aliases",
            "is_active",
        ]


class SkillWriteSerializer(serializers.ModelSerializer):
    """Admin-only: the taxonomy is centrally managed (prompt §21)."""

    class Meta:
        model = Skill
        fields = [
            "id",
            "slug",
            "category",
            "name_uz",
            "name_ru",
            "name_en",
            "description_uz",
            "description_ru",
            "description_en",
            "aliases",
            "is_active",
        ]


class CapitalDimensionSerializer(TranslatableModelSerializer):
    class Meta:
        model = CapitalDimension
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "icon",
            "color",
            "order",
            "default_weight",
        ]


class SkillDimensionSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")
    dimension_slug = serializers.CharField(source="dimension.slug", read_only=True)

    class Meta:
        model = SkillDimension
        fields = ["id", "skill", "skill_name", "dimension", "dimension_slug", "weight"]


class ProfessionSkillSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")
    category = serializers.CharField(source="skill.category.slug", read_only=True)

    class Meta:
        model = ProfessionSkill
        fields = [
            "id",
            "skill",
            "skill_name",
            "category",
            "requirement",
            "min_proficiency",
            "weight",
            "order",
        ]


class ProfessionListSerializer(TranslatableModelSerializer):
    required_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Profession
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "category",
            "icon",
            "demand_level",
            "median_salary",
            "currency",
            "is_active",
            "required_count",
        ]


class ProfessionDetailSerializer(ProfessionListSerializer):
    skills = ProfessionSkillSerializer(source="skill_links", many=True, read_only=True)

    class Meta(ProfessionListSerializer.Meta):
        fields = [*ProfessionListSerializer.Meta.fields, "skills"]


class ProfessionWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profession
        fields = [
            "id",
            "slug",
            "category",
            "name_uz",
            "name_ru",
            "name_en",
            "description_uz",
            "description_ru",
            "description_en",
            "icon",
            "demand_level",
            "median_salary",
            "currency",
            "is_active",
        ]
