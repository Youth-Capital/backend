"""Abstract base models used by every domain app."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import get_language


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(TimeStampedModel):
    """UUID primary keys everywhere.

    Sequential integer ids leak volume ("how many students do you have?") and
    make IDOR probing trivial. UUIDs cost a little index size and remove both.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)

    def delete(self):
        return self.update(deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).alive()


class SoftDeleteModel(BaseModel):
    """Rows that must survive deletion for audit or outcome reporting."""

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager.from_queryset(SoftDeleteQuerySet)()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])

    def hard_delete(self, using=None, keep_parents=False):
        super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        self.deleted_at = None
        self.save(update_fields=["deleted_at", "updated_at"])


def resolve_translation(instance, field: str, language: str | None = None) -> str:
    """Return ``<field>_<lang>`` with a fallback chain.

    Falls back through settings.LANGUAGE_FALLBACK_ORDER (uz -> ru -> en) so a
    partially translated taxonomy row still renders something readable rather
    than an empty label.
    """
    language = language or get_language() or settings.LANGUAGE_CODE
    language = language.split("-")[0]

    order = [language, *settings.LANGUAGE_FALLBACK_ORDER]
    seen: set[str] = set()
    for lang in order:
        if lang in seen:
            continue
        seen.add(lang)
        value = getattr(instance, f"{field}_{lang}", None)
        if value:
            return value
    return ""


class TranslatableNameMixin(models.Model):
    """Explicit per-language columns for platform-managed taxonomy.

    Chosen over a JSON blob because these fields are searched, sorted and
    indexed; over a translations table because the language set is fixed at
    three and a join per label is not worth it.
    """

    name_uz = models.CharField(max_length=255)
    name_ru = models.CharField(max_length=255, blank=True)
    name_en = models.CharField(max_length=255, blank=True)

    description_uz = models.TextField(blank=True)
    description_ru = models.TextField(blank=True)
    description_en = models.TextField(blank=True)

    class Meta:
        abstract = True

    @property
    def name(self) -> str:
        return resolve_translation(self, "name")

    @property
    def description(self) -> str:
        return resolve_translation(self, "description")

    def __str__(self) -> str:
        return self.name or self.name_uz
