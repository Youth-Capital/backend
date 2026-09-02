"""Courses, lessons, enrolment and certificates (prompt §7)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import Language, ModerationStatus
from apps.common.models import BaseModel
from apps.common.validators import validate_image_upload
from apps.profiles.models import EmployerProfile
from apps.taxonomy.models import Skill, SkillCategory


class CourseLevel(models.TextChoices):
    BEGINNER = "BEGINNER", _("Beginner")
    INTERMEDIATE = "INTERMEDIATE", _("Intermediate")
    ADVANCED = "ADVANCED", _("Advanced")


class ProviderType(models.TextChoices):
    PLATFORM = "PLATFORM", _("Platform")
    EMPLOYER = "EMPLOYER", _("Employer")
    PARTNER = "PARTNER", _("Partner")
    INSTITUTION = "INSTITUTION", _("Educational institution")


class Course(BaseModel):
    slug = models.SlugField(max_length=160, unique=True)
    title = models.CharField(max_length=255)

    #: Who supplied it. Null for courses authored on the platform itself.
    provider = models.ForeignKey(
        "learning.ContentProvider",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="courses",
    )
    #: The partner's own id. Re-import updates this row instead of adding a
    #: second one — partners resend corrected batches, and that must be safe.
    external_id = models.CharField(max_length=120, blank=True, db_index=True)
    #: Shared by the Uzbek, Russian and English versions of one course, so the
    #: catalogue shows a single entry and opens the reader's language.
    translation_group = models.UUIDField(null=True, blank=True, db_index=True)
    summary = models.CharField(max_length=400, blank=True)
    description = models.TextField(blank=True)
    language = models.CharField(
        max_length=2, choices=Language.choices, default=Language.UZ
    )

    provider_type = models.CharField(
        max_length=12, choices=ProviderType.choices, default=ProviderType.PLATFORM
    )
    employer = models.ForeignKey(
        EmployerProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="courses",
    )
    #: Null for imported content: a partner's course has no author on this
    #: platform, and inventing one would put a real person's name on work they
    #: did not write.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="authored_courses",
    )
    #: Also null until reviewed. An imported course may not map onto our
    #: taxonomy yet, and guessing a category would mis-file it in the
    #: catalogue and skew every recommendation drawn from it.
    category = models.ForeignKey(
        SkillCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="courses",
    )

    level = models.CharField(
        max_length=14, choices=CourseLevel.choices, default=CourseLevel.BEGINNER
    )
    duration_minutes = models.PositiveIntegerField(default=0)
    cover_image = models.ImageField(
        upload_to="courses/", blank=True, null=True, validators=[validate_image_upload]
    )

    status = models.CharField(
        max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.DRAFT
    )
    moderation_note = models.TextField(blank=True)
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderated_courses",
    )
    published_at = models.DateTimeField(null=True, blank=True)

    is_certified = models.BooleanField(default=False)
    enrollment_count = models.PositiveIntegerField(default=0)
    completion_count = models.PositiveIntegerField(default=0)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "learning_course"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "language"]),
            models.Index(fields=["employer", "status"]),
            models.Index(fields=["category", "status"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_published(self) -> bool:
        return self.status == ModerationStatus.PUBLISHED

    @property
    def completion_rate(self) -> int:
        if not self.enrollment_count:
            return 0
        return round(100 * self.completion_count / self.enrollment_count)


class CourseSkill(BaseModel):
    """What finishing this course says about the learner's skills."""

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="skill_links"
    )
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="course_links")
    target_proficiency = models.PositiveSmallIntegerField(default=60)
    weight = models.DecimalField(max_digits=4, decimal_places=2, default=1.0)

    class Meta:
        db_table = "learning_course_skill"
        constraints = [
            models.UniqueConstraint(fields=["course", "skill"], name="uniq_course_skill")
        ]


class CourseModule(BaseModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "learning_course_module"
        ordering = ["order", "created_at"]

    def __str__(self) -> str:
        return f"{self.course.title} · {self.title}"


class Lesson(BaseModel):
    module = models.ForeignKey(
        CourseModule, on_delete=models.CASCADE, related_name="lessons"
    )
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    video_url = models.URLField(blank=True)
    #: What is said in the video, for the recap to summarise.
    #
    # The platform cannot hear a YouTube video: it holds a link, not the audio.
    # So a recap has to be built from text somebody supplied, and this is where
    # it goes — YouTube hands the author a transcript, and pasting it here is
    # a great deal more honest than a model guessing from the title.
    transcript = models.TextField(blank=True)

    #: The generated revision recap, and a fingerprint of the text it was made
    #: from. Cached because it does not change until the lesson does, and a
    #: model call per learner per revision is a bill for the same sentences.
    #: The fingerprint is what makes it self-invalidating: edit the lesson and
    #: the stored recap no longer matches, so it is regenerated rather than
    #: quietly describing the old version.
    recap = models.TextField(blank=True)
    recap_source_hash = models.CharField(max_length=32, blank=True)
    recap_generated_at = models.DateTimeField(null=True, blank=True)

    #: A few questions checking that the lesson landed, generated from the same
    #: text as the recap and cached the same way. Shape:
    #: [{"question": str, "options": [str, ...], "answer": int, "why": str}]
    #:
    #: The answer index never leaves the server: the questions are served
    #: without it and graded here. A quiz whose answers are in the page is a
    #: quiz that measures nothing.
    check_questions = models.JSONField(default=list, blank=True)
    check_source_hash = models.CharField(max_length=32, blank=True)

    duration_minutes = models.PositiveSmallIntegerField(default=10)
    order = models.PositiveSmallIntegerField(default=0)
    is_free_preview = models.BooleanField(default=False)

    class Meta:
        db_table = "learning_lesson"
        ordering = ["order", "created_at"]

    def __str__(self) -> str:
        return self.title


class EnrollmentStatus(models.TextChoices):
    ENROLLED = "ENROLLED", _("Enrolled")
    IN_PROGRESS = "IN_PROGRESS", _("In progress")
    COMPLETED = "COMPLETED", _("Completed")
    DROPPED = "DROPPED", _("Dropped")


class Enrollment(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="enrollments"
    )
    status = models.CharField(
        max_length=12, choices=EnrollmentStatus.choices, default=EnrollmentStatus.ENROLLED
    )
    progress = models.PositiveSmallIntegerField(default=0)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "learning_enrollment"
        ordering = ["-enrolled_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "course"], name="uniq_enrollment"),
            models.CheckConstraint(
                condition=models.Q(progress__gte=0, progress__lte=100),
                name="enrollment_progress_range",
            ),
        ]
        indexes = [
            models.Index(fields=["course", "status"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} · {self.course.title} ({self.progress}%)"


class LessonProgressStatus(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", _("Not started")
    IN_PROGRESS = "IN_PROGRESS", _("In progress")
    COMPLETED = "COMPLETED", _("Completed")


class LessonProgress(BaseModel):
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE, related_name="lesson_progress"
    )
    lesson = models.ForeignKey(
        Lesson, on_delete=models.CASCADE, related_name="progress_records"
    )
    status = models.CharField(
        max_length=12,
        choices=LessonProgressStatus.choices,
        default=LessonProgressStatus.NOT_STARTED,
    )
    seconds_spent = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    #: How the comprehension check went, out of 100. Null means not attempted.
    #: Kept per learner rather than on the lesson, and kept at all so the
    #: employer's course analytics can say whether a lesson is landing.
    check_score = models.PositiveSmallIntegerField(null=True, blank=True)
    check_attempts = models.PositiveSmallIntegerField(default=0)
    check_answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "learning_lesson_progress"
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment", "lesson"], name="uniq_lesson_progress"
            )
        ]


class Certificate(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="certificates"
    )
    course = models.ForeignKey(
        Course, on_delete=models.PROTECT, related_name="certificates"
    )
    serial = models.CharField(max_length=32, unique=True)
    verification_code = models.CharField(max_length=32, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    file = models.FileField(upload_to="certificates/", blank=True, null=True)

    class Meta:
        db_table = "learning_certificate"
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "course"], name="uniq_certificate_per_course"
            )
        ]

    def __str__(self) -> str:
        return self.serial


class LessonNote(BaseModel):
    """A student's own note, taken while studying.

    Private by construction: every query is filtered by `user`, and the API
    never exposes a route that could return someone else's row. Notes are not
    course content — an author or admin has no business reading them.

    `lesson` is nullable, and deliberately SET_NULL rather than CASCADE. A
    content partner re-imports their course as a whole file, so a lesson can
    disappear between one import and the next; losing the student's own
    writing because a publisher reorganised a syllabus would be indefensible.
    A note whose lesson is gone becomes a course-level note and stays readable.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="course_notes"
    )
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="notes")
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notes",
    )
    #: Optional. A note is worth keeping for its body; forcing a headline
    #: first is the kind of friction that stops people writing anything.
    title = models.CharField(max_length=160, blank=True)
    content = models.TextField()

    class Meta:
        db_table = "learning_lesson_note"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "-updated_at"]),
            models.Index(fields=["user", "course"]),
            models.Index(fields=["user", "lesson"]),
        ]

    def __str__(self) -> str:
        return self.title or self.content[:40]


# Content partners live in their own module for readability; re-exported here
# so every learning model is importable from one place.
from .providers import (  # noqa: E402
    AttachmentKind,
    ContentImport,
    ContentProvider,
    ImportStatus,
    LessonAttachment,
    new_translation_group,
)

__all__ = [
    "AttachmentKind",
    "ContentImport",
    "ContentProvider",
    "ImportStatus",
    "LessonAttachment",
    "new_translation_group",
]


class MaterialKind(models.TextChoices):
    FILE = "FILE", _("File")
    LINK = "LINK", _("Link")
    BOOK = "BOOK", _("Book")


class CourseMaterial(BaseModel):
    """Something to read or download alongside a lesson.

    Attached to a course, and optionally narrowed to one lesson: a reading list
    belongs to the course, a worksheet belongs to the lesson it is used in, and
    forcing either shape on the other produces a mess of duplicates.

    A material is a file *or* a link, never both. Books are links with a
    different label — an author listing "Clean Code, chapter 3" is pointing at
    something the platform does not host, and pretending otherwise would put a
    download button in front of a book we do not have.

    Downloads are gated by the same rule as lesson bodies: whoever may open the
    course may take its materials. Uploaded files are validated on the way in
    (apps/common/validators), because this is a place where a stranger hands
    the server a file.
    """

    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="materials"
    )
    #: Null means "belongs to the whole course" rather than to one lesson.
    lesson = models.ForeignKey(
        "Lesson",
        on_delete=models.CASCADE,
        related_name="materials",
        null=True,
        blank=True,
    )

    kind = models.CharField(
        max_length=8, choices=MaterialKind.choices, default=MaterialKind.LINK
    )
    title = models.CharField(max_length=255)
    description = models.CharField(max_length=500, blank=True)

    file = models.FileField(upload_to="course-materials/", blank=True, null=True)
    url = models.URLField(blank=True)

    order = models.PositiveSmallIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_materials",
    )

    class Meta:
        db_table = "learning_course_material"
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["course", "lesson"]),
        ]
        constraints = [
            # A file or a link, and exactly one of them. Without this a row can
            # exist that renders as neither, and the page has nothing to show.
            models.CheckConstraint(
                condition=(
                    models.Q(kind="FILE", file__isnull=False)
                    | models.Q(kind__in=["LINK", "BOOK"])
                ),
                name="material_file_kind_has_a_file",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.course_id} · {self.title}"

    @property
    def is_download(self) -> bool:
        return self.kind == MaterialKind.FILE
