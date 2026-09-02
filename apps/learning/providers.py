"""Content partners and their imports.

Partners deliver the same course in Uzbek, Russian and English. Three
separate rows with no link between them would look like three unrelated
courses: the catalogue would triple, a learner switching language would lose
their place, and progress would not carry across.

So a course carries two extra identities:

* `external_id` — the partner's own id, which makes re-import an update rather
  than a duplicate. Partners will resend corrected files; that has to be safe.
* `translation_group` — shared by the language variants of one course, so the
  catalogue can show one entry and open the reader's language.
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel


class ContentProvider(BaseModel):
    """An organisation that supplies courses.

    Kept as a row rather than a string on the course so a partner can be
    deactivated, credited, and reported on — "which partner's content actually
    leads to employment" is a question the platform is built to answer.
    """

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=80, unique=True)
    website = models.URLField(blank=True)
    contact_email = models.EmailField(blank=True)

    #: Shown on every course from this partner.
    logo_url = models.URLField(blank=True)
    description = models.TextField(blank=True, max_length=2000)

    #: Content from an inactive provider stays visible to those already
    #: enrolled but is withdrawn from the catalogue — pulling a course out
    #: from under a learner mid-way is worse than letting them finish.
    is_active = models.BooleanField(default=True)
    accepts_imports = models.BooleanField(default=True)

    class Meta:
        db_table = "learning_content_provider"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class ImportStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    RUNNING = "RUNNING", _("Running")
    SUCCEEDED = "SUCCEEDED", _("Succeeded")
    PARTIAL = "PARTIAL", _("Completed with errors")
    FAILED = "FAILED", _("Failed")


class ContentImport(BaseModel):
    """One delivery from a partner.

    Every run is recorded with its counts and its errors. When a partner asks
    "did our March batch land", the answer is a row, not a memory.
    """

    provider = models.ForeignKey(
        ContentProvider, on_delete=models.CASCADE, related_name="imports"
    )
    source = models.CharField(max_length=255, help_text=_("File name or endpoint."))
    status = models.CharField(
        max_length=12, choices=ImportStatus.choices, default=ImportStatus.PENDING
    )

    courses_created = models.PositiveIntegerField(default=0)
    courses_updated = models.PositiveIntegerField(default=0)
    lessons_written = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)

    #: [{"course": "...", "error": "..."}] — one row per rejected item, so a
    #: partner can be told exactly which entries to fix rather than "it failed".
    errors = models.JSONField(default=list, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "learning_content_import"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.provider.slug}:{self.source} [{self.status}]"


class AttachmentKind(models.TextChoices):
    PDF = "PDF", _("PDF")
    SLIDES = "SLIDES", _("Slides")
    WORKSHEET = "WORKSHEET", _("Worksheet")
    DATASET = "DATASET", _("Dataset")
    CODE = "CODE", _("Code")
    LINK = "LINK", _("External link")
    OTHER = "OTHER", _("Other")


class LessonAttachment(BaseModel):
    """A file or link that belongs to a lesson.

    Held as a URL rather than an upload: partners deliver material that already
    lives on their own storage, and copying gigabytes of video to re-host it
    would make every import slow and every correction a re-upload.
    """

    lesson = models.ForeignKey(
        "learning.Lesson", on_delete=models.CASCADE, related_name="attachments"
    )
    kind = models.CharField(
        max_length=12, choices=AttachmentKind.choices, default=AttachmentKind.OTHER
    )
    title = models.CharField(max_length=200)
    url = models.URLField(max_length=800)
    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    language = models.CharField(max_length=2, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    #: The partner's id for this file, so re-import updates in place.
    external_id = models.CharField(max_length=120, blank=True, db_index=True)

    class Meta:
        db_table = "learning_lesson_attachment"
        ordering = ("order", "title")
        constraints = [
            models.UniqueConstraint(
                fields=("lesson", "external_id"),
                condition=models.Q(external_id__gt=""),
                name="learning_attachment_unique_external",
            )
        ]

    def __str__(self) -> str:
        return self.title


def new_translation_group() -> uuid.UUID:
    return uuid.uuid4()
