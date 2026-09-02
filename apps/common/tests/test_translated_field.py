"""Regression tests for TranslatedField.

The field forced `source="*"`, which silently overrode any source the caller
passed. Every `TranslatedField("name", source="skill")` in the project — skill
names, category names, region names, profession names — resolved against the
parent row instead of the related object and returned "". The UI showed skill
cards with no skill name on them and nothing errored.
"""

import pytest
from rest_framework import serializers

from apps.common.serializers import TranslatedField
from apps.profiles.models import UserSkill

pytestmark = pytest.mark.django_db


class UserSkillProbe(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")
    category_name = TranslatedField("name", source="skill.category")

    class Meta:
        model = UserSkill
        fields = ["proficiency", "skill_name", "category_name"]


def test_related_source_is_honoured(student, taxonomy, give_skill):
    """A source pointing at a relation must resolve against that relation."""
    give_skill(student, taxonomy["sql"], 80)
    user_skill = UserSkill.objects.get(user=student, skill=taxonomy["sql"])

    data = UserSkillProbe(user_skill).data

    assert data["skill_name"] == "SQL"
    assert data["category_name"] == "Ma'lumotlar"


def test_default_source_still_resolves_against_the_instance(taxonomy):
    """Omitting source keeps the original behaviour for taxonomy serializers."""

    class SkillProbe(serializers.Serializer):
        name = TranslatedField("name")

    assert SkillProbe(taxonomy["sql"]).data["name"] == "SQL"


def test_falls_back_when_the_requested_language_is_empty(taxonomy):
    """Fallback chain uz -> ru -> en, so a partial translation still renders."""
    from django.utils.translation import override

    skill = taxonomy["sql"]
    skill.name_ru = ""
    skill.name_en = "Structured Query Language"
    skill.save(update_fields=["name_ru", "name_en"])

    class SkillProbe(serializers.Serializer):
        name = TranslatedField("name")

    with override("ru"):
        # ru is blank, so it falls back to uz rather than rendering nothing.
        assert SkillProbe(skill).data["name"] == "SQL"


def test_api_returns_skill_names(auth, student, taxonomy, give_skill):
    """The end-to-end shape the Skills page actually consumes."""
    give_skill(student, taxonomy["sql"], 80)

    response = auth(student).get("/api/v1/me/skills/")

    assert response.status_code == 200
    rows = response.json()["results"]
    assert rows, "expected the declared skill to come back"
    assert rows[0]["skill_name"] == "SQL"
    assert rows[0]["category_name"] != ""
