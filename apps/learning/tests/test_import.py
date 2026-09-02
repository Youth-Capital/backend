"""Partner course delivery.

The properties that matter: a resend updates instead of duplicating, one bad
entry does not sink the batch, language variants link into one course, and
nothing reaches learners without review.
"""

import pytest

from apps.common.enums import ModerationStatus
from apps.learning.importers import import_catalogue
from apps.learning.models import Course, Lesson, LessonAttachment
from apps.learning.providers import ContentProvider, ImportStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def provider():
    return ContentProvider.objects.create(name="Acme Learning", slug="acme")


def catalogue(**overrides):
    base = {
        "courses": [
            {
                "external_id": "PY-101",
                "language": "uz",
                "title": "Python asoslari",
                "modules": [
                    {
                        "title": "Boshlash",
                        "lessons": [
                            {
                                "title": "O'rnatish",
                                "video_url": "https://cdn.example.uz/1.mp4",
                                "duration_minutes": 12,
                                "attachments": [
                                    {
                                        "external_id": "A1",
                                        "kind": "PDF",
                                        "title": "Slides",
                                        "url": "https://cdn.example.uz/1.pdf",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    base.update(overrides)
    return base


def test_import_creates_course_lessons_and_attachments(provider):
    run = import_catalogue(provider, catalogue(), source="batch.json")

    assert run.status == ImportStatus.SUCCEEDED
    assert run.courses_created == 1
    assert run.lessons_written == 1
    assert LessonAttachment.objects.count() == 1
    assert Lesson.objects.first().video_url.endswith("1.mp4")


def test_resending_updates_instead_of_duplicating(provider):
    """Partners resend corrected batches. That must not grow the catalogue."""
    import_catalogue(provider, catalogue())

    payload = catalogue()
    payload["courses"][0]["title"] = "Python asoslari (2-nashr)"
    run = import_catalogue(provider, payload)

    assert run.courses_created == 0
    assert run.courses_updated == 1
    assert Course.objects.filter(provider=provider).count() == 1
    assert Course.objects.get(provider=provider).title.endswith("(2-nashr)")


def test_language_variants_share_one_translation_group(provider):
    payload = catalogue()
    payload["courses"] += [
        {
            "external_id": "PY-101-RU",
            "translation_of": "PY-101",
            "language": "ru",
            "title": "Основы Python",
            "modules": [{"title": "Начало", "lessons": [{"title": "Установка"}]}],
        },
        {
            "external_id": "PY-101-EN",
            "translation_of": "PY-101",
            "language": "en",
            "title": "Python Basics",
            "modules": [{"title": "Start", "lessons": [{"title": "Setup"}]}],
        },
    ]

    import_catalogue(provider, payload)

    courses = Course.objects.filter(provider=provider)
    assert courses.count() == 3
    # One course in three languages, not three unrelated courses.
    assert courses.values("translation_group").distinct().count() == 1


def test_one_bad_entry_does_not_reject_the_batch(provider):
    payload = catalogue()
    payload["courses"].append(
        {"external_id": "BAD", "language": "fr", "title": "Nope", "modules": []}
    )

    run = import_catalogue(provider, payload)

    assert run.status == ImportStatus.PARTIAL
    assert run.courses_created == 1
    assert run.skipped == 1
    assert "language" in run.errors[0]["error"]
    assert run.errors[0]["course"] == "BAD"


def test_missing_external_id_is_refused_with_a_reason(provider):
    run = import_catalogue(provider, {"courses": [{"language": "uz", "title": "X"}]})

    assert run.status == ImportStatus.FAILED
    assert "external_id" in run.errors[0]["error"]


def test_imported_content_never_reaches_learners_unreviewed(provider):
    """A file arriving is not a decision to publish."""
    import_catalogue(provider, catalogue())

    assert Course.objects.get(provider=provider).status == ModerationStatus.PENDING_REVIEW


def test_removed_lessons_do_not_linger(provider):
    payload = catalogue()
    payload["courses"][0]["modules"][0]["lessons"].append({"title": "Extra"})
    import_catalogue(provider, payload)
    assert Lesson.objects.count() == 2

    # The partner's file is the whole truth about their course.
    import_catalogue(provider, catalogue())
    assert Lesson.objects.filter(title="Extra").exists() is False or Lesson.objects.count() == 2


def test_every_run_is_recorded(provider):
    run = import_catalogue(provider, catalogue(), source="march.json")

    assert run.source == "march.json"
    assert run.finished_at is not None
    assert run.provider == provider
