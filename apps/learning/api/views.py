"""Learning endpoints."""

import logging

from django.db.models import Q
from django.utils.translation import get_language
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.enums import ModerationStatus, Role
from apps.common.exceptions import DomainError, NotAllowed
from apps.common.permissions import IsAdmin

from ..models import Certificate, Course, CourseSkill, Enrollment, Lesson, LessonNote
from ..services import (
    can_open_course,
    can_open_lesson,
    complete_lesson,
    enroll,
    generate_course_slug,
    get_course_analytics,
    moderate_course,
    submit_for_review,
)
from .serializers import (
    CertificateSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    CourseMaterialSerializer,
    CourseModuleWriteSerializer,
    CourseSkillSerializer,
    CourseWriteSerializer,
    EnrollmentSerializer,
    LessonDetailSerializer,
    LessonNoteSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["learning"])
class CourseViewSet(viewsets.ModelViewSet):
    """Catalogue for learners, authoring surface for employers and admins."""

    permission_classes = [IsAuthenticated]
    filterset_fields = ["category", "level", "language", "provider_type", "is_certified"]
    search_fields = ["title", "summary", "description"]
    ordering_fields = ["published_at", "rating_avg", "enrollment_count", "duration_minutes"]

    def get_queryset(self):
        user = self.request.user
        base = Course.objects.select_related("employer", "category").prefetch_related(
            "skill_links__skill"
        )

        if self.action in {"retrieve", "modules"}:
            base = base.prefetch_related("modules__lessons", "tests")

        # Everyone sees published courses. Admins see everything, including
        # the moderation queue.
        if user.is_admin:
            queryset = base
        elif user.role == Role.EMPLOYER:
            company = getattr(user, "employer_profile", None)
            mine = Q(author=user)
            if company is not None:
                mine = mine | Q(employer=company)

            if self.request.query_params.get("scope") == "catalogue":
                # Deliberate opt-in: browsing what the platform offers.
                queryset = base.filter(Q(status=ModerationStatus.PUBLISHED) | mine)
            else:
                # A company's course page is a management surface, so it shows
                # that company's courses — drafts included.
                #
                # It used to add every published course on the platform, which
                # is how one employer's page came to list six other companies'
                # courses beside their own, each with an "Analytics" button
                # that answers 403. Nothing leaked; the list simply had no
                # business offering the row. Same fault as the vacancy list.
                queryset = base.filter(mine)
        else:
            queryset = base.filter(status=ModerationStatus.PUBLISHED)

        skill = self.request.query_params.get("skill")
        if skill:
            queryset = queryset.filter(skill_links__skill_id=skill)

        moderation_status = self.request.query_params.get("status")
        if moderation_status and user.is_admin:
            queryset = queryset.filter(status=moderation_status)

        return queryset.distinct()

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return CourseWriteSerializer
        if self.action == "retrieve":
            return CourseDetailSerializer
        return CourseListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        user = self.request.user
        if user.is_authenticated and user.role == Role.STUDENT:
            context["enrollments_by_course"] = {
                e.course_id: e for e in Enrollment.objects.filter(user=user)
            }
        return context

    def perform_create(self, serializer):
        user = self.request.user
        if user.role not in {Role.EMPLOYER, Role.ADMIN} and not user.is_superuser:
            raise NotAllowed("Only employers and admins can create courses.")

        company = getattr(user, "employer_profile", None)
        serializer.save(
            author=user,
            employer=company,
            provider_type="EMPLOYER" if company else "PLATFORM",
            slug=generate_course_slug(serializer.validated_data["title"]),
        )

    def perform_update(self, serializer):
        course = self.get_object()
        self._assert_can_edit(course)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_can_edit(instance)
        instance.status = ModerationStatus.ARCHIVED
        instance.save(update_fields=["status", "updated_at"])

    def _assert_can_edit(self, course: Course) -> None:
        user = self.request.user
        if user.is_admin:
            return
        company = getattr(user, "employer_profile", None)
        if course.author_id == user.id or (company and course.employer_id == company.id):
            return
        raise NotAllowed("You cannot edit this course.")

    # -- learner actions -------------------------------------------------
    @extend_schema(request=None, responses={201: EnrollmentSerializer})
    @action(detail=True, methods=["post"])
    def enroll(self, request, pk=None):
        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import consume

        consume(request.user, BillingFeature.COURSE_ENROLLMENT)
        enrollment = enroll(request.user, self.get_object())
        return Response(
            EnrollmentSerializer(enrollment).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["get"])
    def analytics(self, request, pk=None):
        """Employer-facing course performance (prompt §15)."""
        course = self.get_object()
        self._assert_can_edit(course)
        return Response(get_course_analytics(course))

    # -- authoring workflow ----------------------------------------------
    @extend_schema(request=None, responses={200: CourseDetailSerializer})
    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        course = self.get_object()
        self._assert_can_edit(course)
        course = submit_for_review(course, actor=request.user)
        return Response(CourseDetailSerializer(course).data)

    @extend_schema(request=dict, responses={200: CourseDetailSerializer})
    @action(detail=True, methods=["post"], permission_classes=[IsAdmin])
    def moderate(self, request, pk=None):
        course = moderate_course(
            self.get_object(),
            approve=bool(request.data.get("approve")),
            actor=request.user,
            note=request.data.get("note", ""),
        )
        return Response(CourseDetailSerializer(course).data)

    @extend_schema(request=CourseSkillSerializer, responses={201: CourseSkillSerializer})
    @action(detail=True, methods=["post"], url_path="skills")
    def add_skill(self, request, pk=None):
        course = self.get_object()
        self._assert_can_edit(course)
        serializer = CourseSkillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link, _created = CourseSkill.objects.update_or_create(
            course=course,
            skill=serializer.validated_data["skill"],
            defaults={
                "target_proficiency": serializer.validated_data.get(
                    "target_proficiency", 60
                ),
                "weight": serializer.validated_data.get("weight", 1),
            },
        )
        return Response(CourseSkillSerializer(link).data, status=201)


def _questions_without_answers(questions: list[dict]) -> list[dict]:
    """The learner's copy: prompt and options, never the answer index."""
    return [
        {"index": index, "question": item["question"], "options": item["options"]}
        for index, item in enumerate(questions)
    ]


@extend_schema(tags=["learning"])
class LessonViewSet(viewsets.ModelViewSet):
    """Lesson bodies are gated: enrol, or it must be a free preview.

    Reading was guarded from the start; writing was not. This is a
    ModelViewSet, so `create`, `update` and `destroy` came for free with only
    `IsAuthenticated` behind them — any signed-in account, a student included,
    could rewrite the text of any lesson on the platform, point its video
    somewhere else, or delete it outright. Proven, then closed: the course's
    own edit rule now governs its lessons, because a lesson is not a thing
    somebody owns separately from the course it belongs to.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = LessonDetailSerializer

    def get_queryset(self):
        return Lesson.objects.select_related("module__course")

    def _assert_can_edit_course(self, course) -> None:
        user = self.request.user
        if user.is_admin:
            return
        company = getattr(user, "employer_profile", None)
        if course.author_id == user.id or (company and course.employer_id == company.id):
            return
        raise NotAllowed("You cannot edit this course.")

    def perform_create(self, serializer):
        module = serializer.validated_data.get("module")
        if module is None:
            raise NotAllowed("A lesson must belong to a module.")
        self._assert_can_edit_course(module.course)
        serializer.save()

    def perform_update(self, serializer):
        lesson = self.get_object()
        self._assert_can_edit_course(lesson.module.course)
        # A lesson must not be moved into a course the caller cannot edit —
        # otherwise the check above is a formality anybody can step around.
        target = serializer.validated_data.get("module")
        if target is not None and target.id != lesson.module_id:
            self._assert_can_edit_course(target.course)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_can_edit_course(instance.module.course)
        instance.delete()

    def retrieve(self, request, *args, **kwargs):
        lesson = self.get_object()
        if not can_open_lesson(request.user, lesson):
            raise NotAllowed("Enrol in the course to open this lesson.", code="not_enrolled")
        return Response(LessonDetailSerializer(lesson).data)


    @extend_schema(request=None, responses={200: dict})
    @action(detail=True, methods=["post"])
    def recap(self, request, pk=None):
        """A short revision recap of this lesson.

        Built from the lesson's own text — its body and the transcript an
        author pasted — never from the video itself, which the platform cannot
        hear. When there is no text the answer says so; a model asked to
        summarise a video it never watched writes something plausible, and a
        confident wrong recap of a security lesson is worse than none.

        Cached against a fingerprint of that text, so editing the lesson
        regenerates it and re-reading it does not.
        """
        from django.utils import timezone

        from ..recap import (
            build_recap,
            can_recap,
            recap_source,
            source_fingerprint,
        )

        lesson = self.get_object()
        if not can_open_lesson(request.user, lesson):
            raise NotAllowed(
                "Enrol in the course to open this lesson.", code="not_enrolled"
            )

        allowed, reason = can_recap(lesson)
        if not allowed:
            return Response(
                {"available": False, "reason": reason, "recap": ""},
                status=status.HTTP_200_OK,
            )

        fingerprint = source_fingerprint(recap_source(lesson))
        if lesson.recap and lesson.recap_source_hash == fingerprint:
            return Response(
                {
                    "available": True,
                    "recap": lesson.recap,
                    "cached": True,
                    "generated_at": lesson.recap_generated_at,
                }
            )

        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import consume

        # Metered like any other model call, and only when one is actually
        # about to be made — a cached recap costs nobody anything.
        consume(request.user, BillingFeature.AI_CHAT)

        language = {
            "ru": "Russian",
            "en": "English",
            "uz": "Uzbek (latin script)",
        }.get(get_language() or "uz", "Uzbek (latin script)")

        try:
            text = build_recap(lesson, language=language)
        except RuntimeError:
            # No model configured. Honest emptiness, not an invented summary.
            return Response({"available": False, "reason": "no_model", "recap": ""})
        except Exception:
            logger.exception("Recap generation failed for lesson %s", lesson.id)
            raise DomainError(
                "The recap could not be generated. Try again in a minute.",
                code="recap_failed",
            )

        lesson.recap = text
        lesson.recap_source_hash = fingerprint
        lesson.recap_generated_at = timezone.now()
        lesson.save(
            update_fields=[
                "recap",
                "recap_source_hash",
                "recap_generated_at",
                "updated_at",
            ]
        )
        return Response(
            {
                "available": True,
                "recap": text,
                "cached": False,
                "generated_at": lesson.recap_generated_at,
            }
        )


    @extend_schema(request=None, responses={200: dict})
    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        """The questions for this lesson — without the answers.

        Generated from the lesson's own text, cached against a fingerprint of
        it exactly like the recap, and served with the `answer` key stripped.
        The key stays on the server: a quiz whose answers are in the page
        measures nothing, and the page is the first place anyone will look.
        """
        from django.utils import timezone

        from ..recap import (
            build_check,
            can_recap,
            recap_source,
            source_fingerprint,
        )

        lesson = self.get_object()
        if not can_open_lesson(request.user, lesson):
            raise NotAllowed(
                "Enrol in the course to open this lesson.", code="not_enrolled"
            )

        allowed, reason = can_recap(lesson)
        if not allowed:
            return Response({"available": False, "reason": reason, "questions": []})

        fingerprint = source_fingerprint(recap_source(lesson))
        if lesson.check_questions and lesson.check_source_hash == fingerprint:
            return Response(
                {
                    "available": True,
                    "questions": _questions_without_answers(lesson.check_questions),
                }
            )

        from apps.billing.enums import Feature as BillingFeature
        from apps.billing.services import consume

        consume(request.user, BillingFeature.AI_CHAT)

        language = {
            "ru": "Russian",
            "en": "English",
            "uz": "Uzbek (latin script)",
        }.get(get_language() or "uz", "Uzbek (latin script)")

        try:
            questions = build_check(lesson, language=language)
        except RuntimeError:
            return Response({"available": False, "reason": "no_model", "questions": []})
        except Exception:
            logger.exception("Check generation failed for lesson %s", lesson.id)
            raise DomainError(
                "The questions could not be generated. Try again in a minute.",
                code="check_failed",
            )

        if not questions:
            # The model produced nothing usable. Better to say so than to show
            # a broken widget the learner cannot answer.
            return Response({"available": False, "reason": "no_questions", "questions": []})

        lesson.check_questions = questions
        lesson.check_source_hash = fingerprint
        lesson.save(update_fields=["check_questions", "check_source_hash", "updated_at"])

        return Response(
            {"available": True, "questions": _questions_without_answers(questions)}
        )

    @extend_schema(request=dict, responses={200: dict})
    @action(detail=True, methods=["post"], url_path="check/submit")
    def check_submit(self, request, pk=None):
        """Mark the attempt and record the score.

        Answers arrive as {"0": 2, "1": 0, ...}. Grading is done here against
        the stored key, and the feedback returned says which option was right
        and why — a wrong answer with no explanation teaches nothing.
        """
        from django.utils import timezone

        from ..models import Enrollment, LessonProgress
        from ..recap import grade, recap_source, source_fingerprint

        lesson = self.get_object()
        if not can_open_lesson(request.user, lesson):
            raise NotAllowed(
                "Enrol in the course to open this lesson.", code="not_enrolled"
            )

        if not lesson.check_questions:
            raise DomainError("This lesson has no questions.", code="no_questions")

        # The lesson may have been edited between asking and answering, which
        # would grade the learner against questions they never saw.
        if lesson.check_source_hash != source_fingerprint(recap_source(lesson)):
            return Response(
                {"stale": True, "score": None, "results": []},
                status=status.HTTP_409_CONFLICT,
            )

        answers = request.data.get("answers")
        if not isinstance(answers, dict):
            raise DomainError("Send the answers.", code="validation_error")

        score, results = grade(lesson.check_questions, answers)

        # Recorded only for someone actually enrolled: an author previewing
        # their own lesson is not a learner, and their attempt would show up in
        # the course's own analytics.
        enrollment = Enrollment.objects.filter(
            user=request.user, course=lesson.module.course
        ).first()
        if enrollment is not None:
            progress, _ = LessonProgress.objects.get_or_create(
                enrollment=enrollment, lesson=lesson
            )
            progress.check_attempts += 1
            # The best attempt stands: the point is whether they understand it
            # now, not whether they understood it the first time.
            progress.check_score = max(progress.check_score or 0, score)
            progress.check_answered_at = timezone.now()
            progress.save(
                update_fields=[
                    "check_attempts",
                    "check_score",
                    "check_answered_at",
                    "updated_at",
                ]
            )

        return Response({"stale": False, "score": score, "results": results})

    @extend_schema(request=dict, responses={200: EnrollmentSerializer})
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        enrollment = complete_lesson(
            request.user,
            self.get_object(),
            seconds_spent=int(request.data.get("seconds_spent", 0)),
        )
        return Response(EnrollmentSerializer(enrollment).data)


@extend_schema(tags=["learning"])
class MyEnrollmentViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EnrollmentSerializer
    filterset_fields = ["status"]

    def get_queryset(self):
        return (
            Enrollment.objects.filter(user=self.request.user)
            .select_related("course", "course__employer", "course__category")
            .prefetch_related("lesson_progress", "course__skill_links__skill")
            .order_by("-last_activity_at", "-enrolled_at")
        )


@extend_schema(tags=["learning"])
class MyCertificateViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CertificateSerializer

    def get_queryset(self):
        return Certificate.objects.filter(user=self.request.user).select_related("course")


@extend_schema(tags=["learning"])
class MyNoteViewSet(viewsets.ModelViewSet):
    """Notes a student writes for themselves while studying.

    Every method reaches its object through `get_queryset`, which is filtered
    to the requesting user. Someone else's note is therefore not forbidden but
    absent — a 404 rather than a 403, so the endpoint cannot be used to learn
    that a given note id exists.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = LessonNoteSerializer
    filterset_fields = ["course", "lesson"]
    search_fields = ["title", "content"]
    ordering_fields = ["updated_at", "created_at"]

    def get_queryset(self):
        return (
            LessonNote.objects.filter(user=self.request.user)
            .select_related("course", "lesson")
            .order_by("-updated_at")
        )

    def perform_create(self, serializer):
        self._assert_can_study(
            serializer.validated_data["course"], serializer.validated_data.get("lesson")
        )
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        # Re-checked on edit: a note can be moved to another lesson, and
        # access is not something to verify once at creation and then trust.
        course = serializer.validated_data.get("course", serializer.instance.course)
        lesson = serializer.validated_data.get("lesson", serializer.instance.lesson)
        self._assert_can_study(course, lesson)
        serializer.save()

    def _assert_can_study(self, course, lesson) -> None:
        """Notes follow the lesson reader's rules, not looser ones.

        Otherwise the note body becomes a side channel: paste a paragraph of a
        course you never enrolled in, save it, read it back forever.
        """
        allowed = (
            can_open_lesson(self.request.user, lesson)
            if lesson is not None
            else can_open_course(self.request.user, course)
        )
        if not allowed:
            raise NotAllowed(
                "Enrol in the course before taking notes on it.", code="not_enrolled"
            )


class CourseAuthoringMixin:
    """One place that answers "may this person change this course?".

    Modules, lessons and materials all hang off a course, and none of them is
    owned separately from it. Three copies of the rule would be three chances
    to write it slightly differently — which is how the lesson endpoints ended
    up with no rule at all.
    """

    def assert_can_edit_course(self, course) -> None:
        user = self.request.user
        if user.is_admin:
            return
        company = getattr(user, "employer_profile", None)
        if course.author_id == user.id or (
            company is not None and course.employer_id == company.id
        ):
            return
        raise NotAllowed("You cannot edit this course.")


@extend_schema(tags=["learning"])
class CourseModuleViewSet(CourseAuthoringMixin, viewsets.ModelViewSet):
    """The sections a course is divided into."""

    permission_classes = [IsAuthenticated]
    serializer_class = CourseModuleWriteSerializer

    def get_queryset(self):
        from ..models import CourseModule

        queryset = CourseModule.objects.select_related("course").prefetch_related(
            "lessons"
        )
        course_id = self.request.query_params.get("course")
        if course_id:
            queryset = queryset.filter(course_id=course_id)
        return queryset

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course is None:
            raise NotAllowed("A module must belong to a course.")
        self.assert_can_edit_course(course)
        serializer.save()

    def perform_update(self, serializer):
        self.assert_can_edit_course(self.get_object().course)
        target = serializer.validated_data.get("course")
        if target is not None and target.id != self.get_object().course_id:
            self.assert_can_edit_course(target)
        serializer.save()

    def perform_destroy(self, instance):
        self.assert_can_edit_course(instance.course)
        instance.delete()


@extend_schema(tags=["learning"])
class CourseMaterialViewSet(CourseAuthoringMixin, viewsets.ModelViewSet):
    """Files, links and reading lists attached to a course or a lesson.

    Reading follows the course: whoever may open the course may take its
    materials. Writing follows authorship.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = CourseMaterialSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        from ..models import CourseMaterial

        queryset = CourseMaterial.objects.select_related("course", "lesson")
        course_id = self.request.query_params.get("course")
        if course_id:
            queryset = queryset.filter(course_id=course_id)
        lesson_id = self.request.query_params.get("lesson")
        if lesson_id:
            queryset = queryset.filter(lesson_id=lesson_id)
        return queryset

    def list(self, request, *args, **kwargs):
        # Materials are only ever asked for in the context of a course, and
        # answering "every material on the platform" would hand out other
        # companies' worksheets to anybody who asked.
        course_id = request.query_params.get("course")
        if not course_id:
            raise DomainError("Name the course.", code="validation_error")

        course = Course.objects.filter(id=course_id).first()
        if course is None:
            raise DomainError("That course was not found.", code="not_found")
        if not can_open_course(request.user, course):
            raise NotAllowed(
                "Enrol in the course to see its materials.", code="not_enrolled"
            )
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        material = self.get_object()
        if not can_open_course(request.user, material.course):
            raise NotAllowed(
                "Enrol in the course to see its materials.", code="not_enrolled"
            )
        return Response(self.get_serializer(material).data)

    def perform_create(self, serializer):
        course = serializer.validated_data.get("course")
        if course is None:
            raise NotAllowed("A material must belong to a course.")
        self.assert_can_edit_course(course)

        lesson = serializer.validated_data.get("lesson")
        if lesson is not None and lesson.module.course_id != course.id:
            raise DomainError(
                "That lesson is not part of this course.", code="validation_error"
            )
        serializer.save(uploaded_by=self.request.user)

    def perform_update(self, serializer):
        self.assert_can_edit_course(self.get_object().course)
        serializer.save()

    def perform_destroy(self, instance):
        self.assert_can_edit_course(instance.course)
        instance.delete()
