"""Learning serializers."""

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.common.serializers import TranslatedField
from apps.common.validators import validate_document_upload

from ..models import (
    Certificate,
    Course,
    CourseMaterial,
    CourseModule,
    CourseSkill,
    Enrollment,
    Lesson,
    LessonNote,
    LessonProgress,
)


class CourseSkillSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")

    class Meta:
        model = CourseSkill
        fields = ["id", "skill", "skill_name", "target_proficiency", "weight"]


class LessonListSerializer(serializers.ModelSerializer):
    """Lesson metadata without the body — the catalogue must not leak content."""

    # Whether there is a video, without shipping the URL to a list. The author
    # needs to see at a glance which lessons still have none, and a learner
    # deciding on a course wants to know it is not a wall of text.
    has_video = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "duration_minutes",
            "order",
            "is_free_preview",
            "has_video",
        ]

    def get_has_video(self, lesson) -> bool:
        return bool(lesson.video_url)


class LessonDetailSerializer(serializers.ModelSerializer):
    """The lesson, plus how its video may be shown.

    `video_url` stays exactly as the author typed it — it is what they will
    edit next time. `video` is the derived, checked description the page
    renders from: the frontend never builds an iframe address out of the raw
    field, because that field is user input.
    """

    video = serializers.SerializerMethodField()
    materials = serializers.SerializerMethodField()
    has_recap_source = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            "id",
            "module",
            "title",
            "content",
            "video_url",
            "video",
            "transcript",
            "has_recap_source",
            "materials",
            "duration_minutes",
            "order",
            "is_free_preview",
        ]

    def get_materials(self, lesson) -> list:
        return CourseMaterialSerializer(
            lesson.materials.all(), many=True, context=self.context
        ).data

    def get_has_recap_source(self, lesson) -> bool:
        """Whether a recap can be produced at all — the button knows before
        it is pressed, rather than after a model call has been paid for."""
        from ..recap import can_recap

        return can_recap(lesson)[0]

    def get_video(self, lesson) -> dict | None:
        from ..video import describe_video

        return describe_video(lesson.video_url)


class CourseModuleSerializer(serializers.ModelSerializer):
    lessons = LessonListSerializer(many=True, read_only=True)

    class Meta:
        model = CourseModule
        fields = ["id", "title", "description", "order", "lessons"]


class CourseListSerializer(serializers.ModelSerializer):
    provider_name = serializers.SerializerMethodField()
    skills = serializers.SerializerMethodField()
    completion_rate = serializers.IntegerField(read_only=True)
    my_enrollment = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            "id",
            "slug",
            "title",
            "summary",
            "language",
            "level",
            "duration_minutes",
            "cover_image",
            "category",
            "provider_type",
            "provider_name",
            "status",
            "is_certified",
            "enrollment_count",
            "completion_rate",
            "rating_avg",
            "rating_count",
            "published_at",
            "skills",
            "my_enrollment",
        ]

    def get_provider_name(self, course) -> str:
        if course.employer_id:
            return course.employer.display_name
        return settings.PLATFORM_NAME

    def get_skills(self, course) -> list[dict]:
        return [
            {"id": str(link.skill_id), "name": link.skill.name}
            for link in course.skill_links.all()
        ]

    def get_my_enrollment(self, course) -> dict | None:
        cache = self.context.get("enrollments_by_course")
        if cache is None:
            return None
        enrollment = cache.get(course.id)
        if enrollment is None:
            return None
        return {
            "id": str(enrollment.id),
            "status": enrollment.status,
            "progress": enrollment.progress,
        }


class CourseDetailSerializer(CourseListSerializer):
    modules = CourseModuleSerializer(many=True, read_only=True)
    tests = serializers.SerializerMethodField()

    class Meta(CourseListSerializer.Meta):
        fields = [
            *CourseListSerializer.Meta.fields,
            "description",
            "modules",
            "tests",
            "moderation_note",
        ]

    def get_tests(self, course) -> list[dict]:
        return [
            {
                "id": str(test.id),
                "title": test.title,
                "passing_score": test.passing_score,
                "time_limit_minutes": test.time_limit_minutes,
                "max_attempts": test.max_attempts,
            }
            for test in course.tests.filter(status="PUBLISHED")
        ]


class CourseWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = [
            "id",
            "title",
            "summary",
            "description",
            "language",
            "category",
            "level",
            "duration_minutes",
            "cover_image",
            "is_certified",
        ]


class LessonProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = LessonProgress
        fields = ["id", "lesson", "status", "seconds_spent", "completed_at"]
        read_only_fields = fields


class EnrollmentSerializer(serializers.ModelSerializer):
    course_detail = CourseListSerializer(source="course", read_only=True)
    lesson_progress = LessonProgressSerializer(many=True, read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            "id",
            "course",
            "course_detail",
            "status",
            "progress",
            "enrolled_at",
            "started_at",
            "completed_at",
            "last_activity_at",
            "lesson_progress",
        ]
        read_only_fields = fields


class CertificateSerializer(serializers.ModelSerializer):
    course_title = serializers.CharField(source="course.title", read_only=True)

    class Meta:
        model = Certificate
        fields = [
            "id",
            "course",
            "course_title",
            "serial",
            "verification_code",
            "issued_at",
            "file",
        ]
        read_only_fields = fields


class LessonNoteSerializer(serializers.ModelSerializer):
    """A student's own note.

    `user` is not a writable field and never will be: ownership comes from the
    request, so no payload can hand a note to somebody else.
    """

    course_title = serializers.CharField(source="course.title", read_only=True)
    lesson_title = serializers.CharField(
        source="lesson.title", read_only=True, default=None
    )

    class Meta:
        model = LessonNote
        fields = [
            "id",
            "course",
            "course_title",
            "lesson",
            "lesson_title",
            "title",
            "content",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_content(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("A note needs something written in it.")
        return value

    def validate(self, attrs):
        """The lesson has to be part of the course the note is filed under.

        Without this, a note could name any lesson id in the database and the
        "Lesson 4" line under it would be a lie — one the student never typed.
        On a partial update either side may be absent, so both fall back to
        what is already stored.
        """
        course = attrs.get("course") or getattr(self.instance, "course", None)
        lesson = attrs.get("lesson", getattr(self.instance, "lesson", None))

        if lesson is not None and course is not None:
            if lesson.module.course_id != course.id:
                raise serializers.ValidationError(
                    {"lesson": "That lesson belongs to a different course."}
                )
        return attrs


class CourseModuleWriteSerializer(serializers.ModelSerializer):
    lessons = LessonListSerializer(many=True, read_only=True)

    class Meta:
        model = CourseModule
        fields = ["id", "course", "title", "description", "order", "lessons"]


class CourseMaterialSerializer(serializers.ModelSerializer):
    """A file or a link. The `file_url` is what a page links to."""

    file_url = serializers.SerializerMethodField()
    file_size = serializers.SerializerMethodField()

    class Meta:
        model = CourseMaterial
        fields = [
            "id",
            "course",
            "lesson",
            "kind",
            "title",
            "description",
            "file",
            "file_url",
            "file_size",
            "url",
            "order",
            "created_at",
        ]
        read_only_fields = ["id", "file_url", "file_size", "created_at"]
        extra_kwargs = {"file": {"write_only": True, "required": False}}

    def get_file_url(self, material) -> str | None:
        if not material.file:
            return None
        request = self.context.get("request")
        url = material.file.url
        return request.build_absolute_uri(url) if request else url

    def get_file_size(self, material) -> int | None:
        if not material.file:
            return None
        try:
            return material.file.size
        except (OSError, ValueError):
            # The row can outlive the file on disk; a missing file is not a
            # reason to fail the whole listing.
            return None

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", "LINK"))
        file = attrs.get("file", getattr(self.instance, "file", None))
        url = attrs.get("url", getattr(self.instance, "url", ""))

        if kind == "FILE":
            if not file:
                raise serializers.ValidationError(
                    {"file": _("Attach a file, or add it as a link instead.")}
                )
            validate_document_upload(file)
        elif not url:
            raise serializers.ValidationError(
                {"url": _("A link is required for this kind of material.")}
            )
        return attrs
