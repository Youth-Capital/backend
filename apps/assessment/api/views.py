"""Assessment endpoints."""

from collections import defaultdict

from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.enums import ModerationStatus, Role
from apps.common.exceptions import NotAllowed
from apps.common.permissions import IsAdmin

from ..models import Question, Test, TestAttempt, TestSkill
from ..services import (
    get_test_analytics,
    moderate_test,
    start_attempt,
    submit_attempt,
)
from .serializers import (
    AttemptSerializer,
    AttemptWithQuestionsSerializer,
    QuestionAdminSerializer,
    SubmitAttemptSerializer,
    TestListSerializer,
    TestSkillSerializer,
    TestWriteSerializer,
)


@extend_schema(tags=["assessment"])
class TestViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    filterset_fields = ["type", "course", "language", "is_public"]
    search_fields = ["title", "description"]

    def get_queryset(self):
        user = self.request.user
        # The question count is annotated rather than counted per row.
        #
        # `test.questions.count()` in the serializer is one query per test, so
        # a page of fifty cost fifty extra round trips and a catalogue of a
        # thousand would have cost a thousand. The count is the same number
        # either way; this asks for it once.
        base = (
            Test.objects.select_related("course", "employer")
            .prefetch_related("skill_links__skill")
            .annotate(questions_total=Count("questions", distinct=True))
        )
        if user.is_admin:
            queryset = base
        elif user.role == Role.EMPLOYER:
            company = getattr(user, "employer_profile", None)
            queryset = base.filter(
                Q(status=ModerationStatus.PUBLISHED)
                | Q(employer=company)
                | Q(author=user)
            )
        else:
            queryset = base.filter(status=ModerationStatus.PUBLISHED, is_public=True)

        moderation_status = self.request.query_params.get("status")
        if moderation_status and user.is_admin:
            queryset = queryset.filter(status=moderation_status)
        # Ordered explicitly, not by inheritance.
        #
        # The annotation above adds a GROUP BY, and Django reports a grouped
        # queryset as unordered even when the model's Meta ordering is still
        # in the SQL. The paginator believes that report and warns that pages
        # may repeat or skip rows — so the ordering is stated here where both
        # the database and the paginator can see it.
        return queryset.distinct().order_by("-created_at")

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return TestWriteSerializer
        return TestListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        user = self.request.user
        if user.is_authenticated and user.role == Role.STUDENT:
            cache = defaultdict(list)
            for attempt in TestAttempt.objects.filter(user=user):
                cache[attempt.test_id].append(attempt)
            context["attempts_by_test"] = cache
        return context

    def perform_create(self, serializer):
        user = self.request.user
        if user.role not in {Role.EMPLOYER, Role.ADMIN} and not user.is_superuser:
            raise NotAllowed("Only employers and admins can create tests.")
        serializer.save(
            author=user, employer=getattr(user, "employer_profile", None)
        )

    def _assert_can_edit(self, test: Test) -> None:
        user = self.request.user
        if user.is_admin:
            return
        company = getattr(user, "employer_profile", None)
        if test.author_id == user.id or (company and test.employer_id == company.id):
            return
        raise NotAllowed("You cannot edit this test.")

    def perform_update(self, serializer):
        self._assert_can_edit(self.get_object())
        serializer.save()

    # -- authoring -------------------------------------------------------
    @extend_schema(responses={200: QuestionAdminSerializer(many=True)})
    @action(detail=True, methods=["get", "post"], url_path="questions")
    def questions(self, request, pk=None):
        test = self.get_object()
        self._assert_can_edit(test)

        if request.method == "GET":
            return Response(
                QuestionAdminSerializer(
                    test.questions.prefetch_related("options"), many=True
                ).data
            )

        serializer = QuestionAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(test=test)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(request=TestSkillSerializer, responses={201: TestSkillSerializer})
    @action(detail=True, methods=["post"], url_path="skills")
    def add_skill(self, request, pk=None):
        test = self.get_object()
        self._assert_can_edit(test)
        serializer = TestSkillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link, _created = TestSkill.objects.update_or_create(
            test=test,
            skill=serializer.validated_data["skill"],
            defaults={"weight": serializer.validated_data.get("weight", 1)},
        )
        return Response(TestSkillSerializer(link).data, status=201)

    @extend_schema(request=None, responses={200: TestListSerializer})
    @action(detail=True, methods=["post"], url_path="submit-for-review")
    def submit_for_review(self, request, pk=None):
        test = self.get_object()
        self._assert_can_edit(test)
        test.status = ModerationStatus.PENDING_REVIEW
        test.save(update_fields=["status", "updated_at"])
        return Response(TestListSerializer(test, context=self.get_serializer_context()).data)

    @extend_schema(request=dict, responses={200: TestListSerializer})
    @action(detail=True, methods=["post"], permission_classes=[IsAdmin])
    def moderate(self, request, pk=None):
        test = moderate_test(
            self.get_object(),
            approve=bool(request.data.get("approve")),
            actor=request.user,
            note=request.data.get("note", ""),
        )
        return Response(TestListSerializer(test, context=self.get_serializer_context()).data)

    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["get"])
    def analytics(self, request, pk=None):
        test = self.get_object()
        self._assert_can_edit(test)
        return Response(get_test_analytics(test))

    @extend_schema(responses={200: dict})
    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        """Ranked participant list for the test owner (prompt §16)."""
        test = self.get_object()
        self._assert_can_edit(test)

        attempts = (
            TestAttempt.objects.filter(test=test, status="GRADED")
            .select_related("user", "user__student_profile")
            .order_by("-percentage")[:200]
        )
        return Response(
            [
                {
                    "user_id": str(a.user_id),
                    "youth_id": getattr(
                        getattr(a.user, "student_profile", None), "youth_id", None
                    ),
                    "attempt_no": a.attempt_no,
                    "percentage": a.percentage,
                    "passed": a.passed,
                    "time_spent_seconds": a.time_spent_seconds,
                    "submitted_at": a.submitted_at,
                }
                for a in attempts
            ]
        )

    # -- taking ----------------------------------------------------------
    @extend_schema(request=None, responses={201: AttemptWithQuestionsSerializer})
    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        attempt = start_attempt(request.user, self.get_object())
        return Response(
            AttemptWithQuestionsSerializer(attempt).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["assessment"])
class AttemptViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AttemptSerializer
    filterset_fields = ["test", "passed", "status"]

    def get_queryset(self):
        return (
            TestAttempt.objects.filter(user=self.request.user)
            .select_related("test")
            .prefetch_related("skill_results__skill")
            .order_by("-started_at")
        )

    @extend_schema(responses={200: AttemptWithQuestionsSerializer})
    def retrieve(self, request, *args, **kwargs):
        attempt = self.get_object()
        if attempt.status == "IN_PROGRESS":
            return Response(AttemptWithQuestionsSerializer(attempt).data)
        return Response(AttemptSerializer(attempt).data)

    @extend_schema(request=SubmitAttemptSerializer, responses={200: dict})
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        serializer = SubmitAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        attempt = submit_attempt(self.get_object(), serializer.validated_data["answers"])

        # Re-read before serialising. `get_object()` prefetched `skill_results`
        # while the attempt was still ungraded — an empty list — and grading
        # then created the rows behind that cache. Serialising the cached
        # object returned a result screen with no per-skill breakdown at all,
        # which is the one thing the screen exists to show.
        attempt = self.get_queryset().get(pk=attempt.pk)
        payload = AttemptSerializer(attempt).data

        # Correct answers are revealed only after submission, and only if the
        # author allowed it.
        if attempt.test.show_correct_answers:
            payload["review"] = [
                {
                    "question_id": str(answer.question_id),
                    "question": answer.question.text,
                    "is_correct": answer.is_correct,
                    "points_awarded": answer.points_awarded,
                    "explanation": answer.question.explanation,
                    "correct_option_ids": [
                        str(o.id) for o in answer.question.options.all() if o.is_correct
                    ],
                    "selected_option_ids": [
                        str(o.id) for o in answer.selected_options.all()
                    ],
                }
                for answer in attempt.answers.select_related("question").prefetch_related(
                    "question__options", "selected_options"
                )
            ]
        return Response(payload)


@extend_schema(tags=["assessment"])
class QuestionViewSet(viewsets.ModelViewSet):
    """Question editing for test owners and admins only."""

    permission_classes = [IsAuthenticated]
    serializer_class = QuestionAdminSerializer

    def get_queryset(self):
        user = self.request.user
        base = Question.objects.select_related("test").prefetch_related("options")
        if user.is_admin:
            return base
        company = getattr(user, "employer_profile", None)
        return base.filter(Q(test__author=user) | Q(test__employer=company))
