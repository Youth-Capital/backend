"""Serializer building blocks."""

from rest_framework import serializers


class TranslatedField(serializers.Field):
    """Read-only field resolving ``<field_name>_<lang>`` with fallback.

    Lets the API expose a flat ``name``/``description`` while the table keeps
    one column per language (docs/02-ARCHITECTURE.md §10).

    `source` defaults to the whole instance but must stay overridable: writing
    ``TranslatedField("name", source="skill")`` reads the *related* object's
    translated name. Forcing ``source="*"`` here made every such field resolve
    against the parent row instead — a UserSkill has no `name_ru`, so the API
    silently returned an empty string and skill names vanished from the UI.
    """

    def __init__(self, field_name: str, **kwargs):
        kwargs["read_only"] = True
        kwargs.setdefault("source", "*")
        self._translated_field = field_name
        super().__init__(**kwargs)

    def to_representation(self, instance) -> str:
        from .models import resolve_translation

        return resolve_translation(instance, self._translated_field)


class TranslationsField(serializers.Field):
    """All three languages at once — for admin editing screens."""

    def __init__(self, field_name: str, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs["source"] = "*"
        self._translated_field = field_name
        super().__init__(**kwargs)

    def to_representation(self, instance) -> dict[str, str]:
        return {
            lang: getattr(instance, f"{self._translated_field}_{lang}", "") or ""
            for lang in ("uz", "ru", "en")
        }


class TranslatableModelSerializer(serializers.ModelSerializer):
    """Adds resolved `name`/`description` plus raw `translations` for admins."""

    name = TranslatedField("name")
    description = TranslatedField("description")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            from .enums import Role

            if user.role == Role.ADMIN or user.is_superuser:
                data["translations"] = {
                    "name": TranslationsField("name").to_representation(instance),
                    "description": TranslationsField("description").to_representation(
                        instance
                    ),
                }
        return data
