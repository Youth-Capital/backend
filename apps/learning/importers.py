"""Importing partner course catalogues.

The format is documented in `docs/08-CONTENT-IMPORT.md` and can be handed to a
partner before any code runs on their side.

Two properties matter more than speed:

**Idempotent.** Keyed on (provider, external_id), so resending a corrected
batch updates rather than duplicates. Partners will resend.

**Partial success.** One malformed course does not reject the batch. Every
rejection is recorded with the reason, so the partner is told which entries to
fix instead of "the import failed".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.common.enums import ModerationStatus

from .models import Course, CourseModule, Lesson
from .providers import (
    AttachmentKind,
    ContentImport,
    ContentProvider,
    ImportStatus,
    LessonAttachment,
)

LANGUAGES = {"uz", "ru", "en"}


@dataclass
class Result:
    created: int = 0
    updated: int = 0
    lessons: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)

    def reject(self, ref: str, reason: str) -> None:
        self.skipped += 1
        self.errors.append({"course": ref, "error": reason})


def _clean_str(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _validate(entry: dict) -> str | None:
    """Return the reason this entry cannot be imported, or None."""
    if not isinstance(entry, dict):
        return "entry is not an object"
    if not entry.get("external_id"):
        return "external_id is required — without it a re-import duplicates"
    if not entry.get("title"):
        return "title is required"
    language = entry.get("language")
    if language not in LANGUAGES:
        return f"language must be one of {sorted(LANGUAGES)}, got {language!r}"
    modules = entry.get("modules")
    if not isinstance(modules, list) or not modules:
        return "at least one module with one lesson is required"
    for module in modules:
        lessons = module.get("lessons") if isinstance(module, dict) else None
        if not isinstance(lessons, list) or not lessons:
            return "every module needs at least one lesson"
    return None


def _resolve_translation_group(provider, entry: dict) -> uuid.UUID:
    """Language variants of one course share a group.

    Partners identify the set with `translation_of` — their id for the course
    this one is a translation of. Falling back to a fresh group means a course
    delivered alone still works; the link can be added on the next import.
    """
    sibling_ref = entry.get("translation_of")
    if sibling_ref:
        sibling = Course.objects.filter(
            provider=provider, external_id=sibling_ref
        ).first()
        if sibling and sibling.translation_group:
            return sibling.translation_group

    existing = Course.objects.filter(
        provider=provider, external_id=entry["external_id"]
    ).first()
    if existing and existing.translation_group:
        return existing.translation_group

    return uuid.uuid4()


def _write_lessons(course: Course, modules: list[dict]) -> int:
    """Replace the course's structure with the delivered one.

    Rebuilt rather than merged: a partner's file is the whole truth about
    their course, and diffing module trees would silently keep lessons they
    deliberately removed. Learner progress is keyed on lessons, so lessons
    that persist keep their external id and are updated in place.
    """
    written = 0
    for module_order, module_entry in enumerate(modules):
        module, _ = CourseModule.objects.update_or_create(
            course=course,
            order=module_order,
            defaults={
                "title": _clean_str(module_entry.get("title"), 255) or f"Module {module_order + 1}",
                "description": _clean_str(module_entry.get("summary"), 2000),
            },
        )

        for lesson_order, lesson_entry in enumerate(module_entry.get("lessons", [])):
            lesson, _ = Lesson.objects.update_or_create(
                module=module,
                order=lesson_order,
                defaults={
                    "title": _clean_str(lesson_entry.get("title"), 255)
                    or f"Lesson {lesson_order + 1}",
                    "content": _clean_str(lesson_entry.get("content"), 20000),
                    "video_url": _clean_str(lesson_entry.get("video_url"), 500),
                    "duration_minutes": int(lesson_entry.get("duration_minutes") or 10),
                    "is_free_preview": bool(lesson_entry.get("is_free_preview")),
                },
            )
            written += 1

            valid_kinds = set(AttachmentKind.values)
            for index, item in enumerate(lesson_entry.get("attachments", []) or []):
                if not isinstance(item, dict) or not item.get("url"):
                    continue
                LessonAttachment.objects.update_or_create(
                    lesson=lesson,
                    external_id=_clean_str(item.get("external_id"), 120),
                    defaults={
                        "kind": item.get("kind")
                        if item.get("kind") in valid_kinds
                        else AttachmentKind.OTHER,
                        "title": _clean_str(item.get("title"), 200) or "Material",
                        "url": _clean_str(item.get("url"), 800),
                        "size_bytes": item.get("size_bytes") or None,
                        "language": _clean_str(item.get("language"), 2),
                        "order": index,
                    },
                )
    return written


@transaction.atomic
def import_course(
    provider: ContentProvider, entry: dict
) -> tuple[Course, bool, int]:
    """Create or update one course. Returns (course, created, lessons_written)."""
    external_id = _clean_str(entry["external_id"], 120)
    language = entry["language"]
    title = _clean_str(entry["title"], 255)

    base_slug = slugify(f"{provider.slug}-{external_id}-{language}")[:150] or str(uuid.uuid4())

    course = Course.objects.filter(provider=provider, external_id=external_id).first()
    created = course is None

    defaults = {
        "title": title,
        "summary": _clean_str(entry.get("summary"), 400),
        "description": _clean_str(entry.get("description"), 20000),
        "language": language,
        "translation_group": _resolve_translation_group(provider, entry),
        # Partner content is never auto-published. Someone on the platform
        # decides what learners see (see docs/01-ANALYSIS.md §3.2).
        "status": ModerationStatus.PENDING_REVIEW,
    }

    # A partner may name a category from our taxonomy; unknown or absent
    # leaves it for the reviewer rather than filing it wrongly.
    category_slug = _clean_str(entry.get("category"), 120)
    if category_slug:
        from apps.taxonomy.models import SkillCategory

        category = SkillCategory.objects.filter(slug=category_slug).first()
        if category is not None:
            defaults["category"] = category

    if created:
        course = Course.objects.create(
            provider=provider, external_id=external_id, slug=base_slug, **defaults
        )
    else:
        for key, value in defaults.items():
            setattr(course, key, value)
        course.save()

    lessons = _write_lessons(course, entry["modules"])
    return course, created, lessons


def import_catalogue(provider: ContentProvider, payload: dict, *, source: str = "") -> ContentImport:
    """Run a whole delivery, recording what happened."""
    run = ContentImport.objects.create(
        provider=provider, source=source[:255], status=ImportStatus.RUNNING
    )
    result = Result()

    courses = payload.get("courses") if isinstance(payload, dict) else None
    if not isinstance(courses, list):
        run.status = ImportStatus.FAILED
        run.errors = [{"course": "-", "error": "payload has no 'courses' array"}]
        run.finished_at = timezone.now()
        run.save()
        return run

    for entry in courses:
        reference = (entry or {}).get("external_id", "?") if isinstance(entry, dict) else "?"
        problem = _validate(entry)
        if problem:
            result.reject(str(reference), problem)
            continue
        try:
            _course, created, lessons = import_course(provider, entry)
        except Exception as error:  # one bad row must not sink the batch
            result.reject(str(reference), f"{type(error).__name__}: {error}"[:300])
            continue

        result.created += created
        result.updated += not created
        result.lessons += lessons

    run.courses_created = result.created
    run.courses_updated = result.updated
    run.lessons_written = result.lessons
    run.skipped = result.skipped
    run.errors = result.errors[:200]
    run.status = (
        ImportStatus.SUCCEEDED
        if not result.errors
        else ImportStatus.PARTIAL
        if (result.created or result.updated)
        else ImportStatus.FAILED
    )
    run.finished_at = timezone.now()
    run.save()
    return run
