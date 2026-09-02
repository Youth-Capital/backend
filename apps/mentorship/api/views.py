"""Mentorship endpoints (TZ epic E10)."""

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.enums import Role, VerificationStatus
from apps.common.exceptions import Conflict, DomainError, NotAllowed
from apps.common.serializers import TranslatedField
from apps.profiles.models import MentorProfile
from apps.taxonomy.models import Skill

from ..models import (
    MentorFeedback,
    MentorSession,
    MentorSkillAssessment,
    PlanReview,
    SessionStatus,
)


class MentorCardSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    expertise_names = serializers.SerializerMethodField()

    class Meta:
        model = MentorProfile
        fields = [
            "id",
            "full_name",
            "headline",
            "bio",
            "avatar",
            "years_experience",
            "is_free",
            "hourly_rate",
            "currency",
            "languages",
            "rating_avg",
            "rating_count",
            "sessions_count",
            "accepting_students",
            "expertise_names",
        ]
        read_only_fields = fields

    def get_expertise_names(self, mentor) -> list[str]:
        return [skill.name for skill in mentor.expertise.all()]


class MentorSkillAssessmentSerializer(serializers.ModelSerializer):
    skill_name = TranslatedField("name", source="skill")

    class Meta:
        model = MentorSkillAssessment
        fields = ["id", "skill", "skill_name", "score"]


class MentorFeedbackSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    skill_assessments = MentorSkillAssessmentSerializer(many=True, read_only=True)

    class Meta:
        model = MentorFeedback
        fields = [
            "id",
            "session",
            "author",
            "author_name",
            "rating",
            "comment",
            "skill_assessments",
            "created_at",
        ]
        read_only_fields = ["id", "author", "created_at"]

    def get_author_name(self, feedback) -> str:
        return feedback.author.display_name


class MentorSessionSerializer(serializers.ModelSerializer):
    mentor_name = serializers.SerializerMethodField()
    student_name = serializers.SerializerMethodField()
    feedback = MentorFeedbackSerializer(many=True, read_only=True)

    class Meta:
        model = MentorSession
        fields = [
            "id",
            "mentor",
            "mentor_name",
            "student",
            "student_name",
            "topic",
            "agenda",
            "scheduled_at",
            "duration_minutes",
            "mode",
            "meeting_link",
            "location",
            "status",
            "notes",
            "declined_reason",
            "feedback",
            "created_at",
        ]
        read_only_fields = ["id", "student", "status", "created_at"]

    def get_mentor_name(self, session) -> str:
        return session.mentor.full_name

    def get_student_name(self, session) -> str:
        profile = getattr(session.student, "student_profile", None)
        return profile.full_name if profile else ""


class PlanReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.SerializerMethodField()

    class Meta:
        model = PlanReview
        fields = ["id", "plan", "reviewer", "reviewer_name", "status", "comment", "created_at"]
        read_only_fields = ["id", "reviewer", "created_at"]

    def get_reviewer_name(self, review) -> str:
        return review.reviewer.display_name


@extend_schema(tags=["mentorship"])
class MentorDirectoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Public mentor catalogue — verified mentors only."""

    permission_classes = [IsAuthenticated]
    serializer_class = MentorCardSerializer
    filterset_fields = ["is_free", "accepting_students"]
    search_fields = ["first_name", "last_name", "headline", "bio"]
    ordering_fields = ["rating_avg", "sessions_count", "years_experience"]

    def get_queryset(self):
        queryset = MentorProfile.objects.filter(
            verification_status=VerificationStatus.VERIFIED
        ).prefetch_related("expertise")

        skill = self.request.query_params.get("skill")
        if skill:
            queryset = queryset.filter(expertise__id=skill)
        profession = self.request.query_params.get("profession")
        if profession:
            queryset = queryset.filter(professions__id=profession)
        return queryset.distinct().order_by("-rating_avg", "-sessions_count")


@extend_schema(tags=["mentorship"])
class MentorSessionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MentorSessionSerializer
    filterset_fields = ["status", "mentor"]

    def get_queryset(self):
        user = self.request.user
        base = MentorSession.objects.select_related(
            "mentor", "student__student_profile"
        ).prefetch_related("feedback__skill_assessments__skill")
        if user.is_admin:
            return base
        if user.role == Role.MENTOR:
            return base.filter(mentor=getattr(user, "mentor_profile", None))
        return base.filter(student=user)

    def perform_create(self, serializer):
        if self.request.user.role != Role.STUDENT:
            raise NotAllowed("Only students can request a mentor session.")

        # The directory only lists verified mentors, but the endpoint accepts any
        # mentor id, so the filter has to live here rather than in the UI.
        mentor = serializer.validated_data.get("mentor")
        if mentor is not None and mentor.verification_status != VerificationStatus.VERIFIED:
            raise NotAllowed("This mentor is not yet verified.")

        session = serializer.save(student=self.request.user)

        from apps.analytics.services import track
        from apps.notifications.services import notify

        track(self.request.user, "mentor_session_requested", {"mentor_id": str(session.mentor_id)})
        notify(
            user=session.mentor.user,
            type="MENTOR_REQUEST",
            title_key="notifications.mentor.request.title",
            body_key="notifications.mentor.request.body",
            payload={"topic": session.topic},
            ref_type="MentorSession",
            ref_id=session.id,
            action_url=f"/mentor/sessions/{session.id}",
        )

    @extend_schema(request=dict, responses={200: MentorSessionSerializer})
    @action(detail=True, methods=["post"], url_path="respond")
    def respond(self, request, pk=None):
        """Mentor accepts or declines a request."""
        session = self.get_object()
        if request.user.role != Role.MENTOR or session.mentor.user_id != request.user.id:
            raise NotAllowed("Only the invited mentor can respond.")
        if session.status != SessionStatus.REQUESTED:
            raise Conflict("This request has already been answered.")

        accept = bool(request.data.get("accept"))
        session.status = SessionStatus.ACCEPTED if accept else SessionStatus.DECLINED
        if accept:
            session.meeting_link = request.data.get("meeting_link", session.meeting_link)
            if request.data.get("scheduled_at"):
                session.scheduled_at = request.data["scheduled_at"]
        else:
            session.declined_reason = request.data.get("reason", "")[:255]
        session.save()

        from apps.notifications.services import notify

        notify(
            user=session.student,
            type="MENTOR_RESPONSE",
            title_key="notifications.mentor.response.title",
            body_key="notifications.mentor.response.body",
            payload={"topic": session.topic, "accepted": accept},
            ref_type="MentorSession",
            ref_id=session.id,
            action_url=f"/student/mentors/sessions/{session.id}",
        )
        return Response(MentorSessionSerializer(session).data)

    @extend_schema(request=None, responses={200: MentorSessionSerializer})
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        session = self.get_object()
        if request.user.role != Role.MENTOR or session.mentor.user_id != request.user.id:
            raise NotAllowed("Only the mentor can close a session.")

        session.status = SessionStatus.COMPLETED
        session.save(update_fields=["status", "updated_at"])

        from apps.analytics.services import track

        track(session.student, "mentor_session_completed", {"session_id": str(session.id)})
        return Response(MentorSessionSerializer(session).data)

    @extend_schema(request=MentorFeedbackSerializer, responses={201: MentorFeedbackSerializer})
    @action(detail=True, methods=["post"])
    def feedback(self, request, pk=None):
        """Leave feedback; a mentor's skill scores become skill evidence."""
        session = self.get_object()
        if request.user.id not in {session.student_id, session.mentor.user_id}:
            raise NotAllowed("You were not part of this session.")
        if session.status != SessionStatus.COMPLETED:
            raise Conflict("Feedback is available after the session is completed.")

        serializer = MentorFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        feedback = serializer.save(session=session, author=request.user)

        # Validated rather than trusted: a missing "skill" key used to raise
        # KeyError and an unknown id an IntegrityError — both surfacing as 500.
        for entry in request.data.get("skill_assessments", []) or []:
            if not isinstance(entry, dict) or not entry.get("skill"):
                raise DomainError(
                    "Each skill assessment needs a skill id.",
                    code="assessment_missing_skill",
                )
            if not Skill.objects.filter(id=entry["skill"]).exists():
                raise DomainError("Unknown skill.", code="unknown_skill")

            try:
                score = int(entry.get("score", 50))
            except (TypeError, ValueError):
                raise DomainError("Score must be a number.", code="invalid_score")

            MentorSkillAssessment.objects.update_or_create(
                feedback=feedback,
                skill_id=entry["skill"],
                defaults={"score": max(0, min(100, score))},
            )

        return Response(
            MentorFeedbackSerializer(feedback).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["mentorship"])
class PlanReviewViewSet(viewsets.ModelViewSet):
    """Mentor review of a development plan (TZ §13)."""

    permission_classes = [IsAuthenticated]
    serializer_class = PlanReviewSerializer

    def get_queryset(self):
        user = self.request.user
        base = PlanReview.objects.select_related("reviewer", "plan")
        if user.is_admin:
            return base
        if user.role == Role.MENTOR:
            return base.filter(reviewer=user)
        return base.filter(plan__user=user)

    def perform_create(self, serializer):
        if self.request.user.role not in {Role.MENTOR, Role.ADMIN}:
            raise NotAllowed("Only mentors and admins can review plans.")

        # A review is not a comment: approving one stamps `approved_by` on the
        # plan, and the platform then treats it as endorsed by a qualified
        # person. The plan id arrives as a uuid in the body and nothing here
        # said whose it was, so any mentor could put their name on any
        # learner's plan without ever having met them.
        plan = serializer.validated_data.get("plan")
        if plan is not None and not self.request.user.is_admin:
            mentor = getattr(self.request.user, "mentor_profile", None)
            works_with = mentor is not None and MentorSession.objects.filter(
                mentor=mentor, student_id=plan.user_id
            ).exists()
            if not works_with:
                raise NotAllowed(
                    "You do not work with this learner.", code="not_your_learner"
                )

        review = serializer.save(reviewer=self.request.user)

        if review.status == "APPROVED":
            plan = review.plan
            plan.approved_by = self.request.user
            plan.save(update_fields=["approved_by", "updated_at"])


@extend_schema(tags=["mentorship"])
class MentorDashboardViewSet(viewsets.ViewSet):
    """Summary for the mentor portal."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        if request.user.role != Role.MENTOR:
            raise NotAllowed("Mentors only.")

        mentor = getattr(request.user, "mentor_profile", None)
        if mentor is None:
            raise NotAllowed("No mentor profile.")

        sessions = MentorSession.objects.filter(mentor=mentor)
        return Response(
            {
                "mentor": MentorCardSerializer(mentor).data,
                "sessions": {
                    "requested": sessions.filter(status=SessionStatus.REQUESTED).count(),
                    "upcoming": sessions.filter(
                        status=SessionStatus.ACCEPTED,
                        scheduled_at__gte=timezone.now(),
                    ).count(),
                    "completed": sessions.filter(status=SessionStatus.COMPLETED).count(),
                },
                "students": sessions.values("student").distinct().count(),
                "pending_reviews": PlanReview.objects.filter(
                    reviewer=request.user, status="PENDING"
                ).count(),
            }
        )

    @extend_schema(responses={200: dict})
    @action(detail=False, methods=["get"], url_path="learners")
    def learners(self, request):
        """The people this mentor actually works with.

        A mentor's list is defined by the sessions they hold, not by a
        subscription: whoever booked a session is who they are responsible for,
        and that same rule governs whose plan they may review and whose profile
        they may open. Deriving the list from sessions keeps those three
        answers from drifting apart.

        Each row carries what the mentor needs before a session — where the
        learner is heading, how far along they are, and whether their plan is
        waiting on a review — rather than a name and a date.
        """
        from apps.idp.models import DevelopmentPlan
        from apps.profiles.models import UserSkill

        mentor = getattr(request.user, "mentor_profile", None)
        if request.user.role != Role.MENTOR or mentor is None:
            raise NotAllowed("Mentors only.")

        sessions = (
            MentorSession.objects.filter(mentor=mentor)
            .select_related("student", "student__student_profile")
            .order_by("-scheduled_at", "-created_at")
        )

        rows: dict = {}
        for session in sessions:
            student = session.student
            row = rows.get(student.id)
            if row is None:
                profile = getattr(student, "student_profile", None)
                row = {
                    "user_id": str(student.id),
                    "youth_id": getattr(profile, "youth_id", None),
                    # A mentor sits with this person: the name is not withheld
                    # the way it is from an employer browsing candidates.
                    "name": getattr(profile, "full_name", "") or student.email,
                    "target_profession": (
                        profile.target_profession.name
                        if profile and profile.target_profession_id
                        else None
                    ),
                    "sessions_total": 0,
                    "sessions_completed": 0,
                    "next_session_at": None,
                    "last_session_at": None,
                    "skills_total": 0,
                    "skills_verified": 0,
                    "plan": None,
                }
                rows[student.id] = row

            row["sessions_total"] += 1
            if session.status == SessionStatus.COMPLETED:
                row["sessions_completed"] += 1
                if session.scheduled_at and (
                    row["last_session_at"] is None
                    or session.scheduled_at > row["last_session_at"]
                ):
                    row["last_session_at"] = session.scheduled_at
            elif (
                session.status == SessionStatus.ACCEPTED
                and session.scheduled_at
                and session.scheduled_at >= timezone.now()
                and (
                    row["next_session_at"] is None
                    or session.scheduled_at < row["next_session_at"]
                )
            ):
                row["next_session_at"] = session.scheduled_at

        # Counted in two queries rather than two per learner: a mentor with
        # thirty students would otherwise cost sixty round trips to render one
        # list.
        student_ids = list(rows)
        skills = UserSkill.objects.filter(user_id__in=student_ids).values(
            "user_id", "status"
        )
        for skill in skills:
            row = rows[skill["user_id"]]
            row["skills_total"] += 1
            if skill["status"] == "VERIFIED":
                row["skills_verified"] += 1

        plans = (
            DevelopmentPlan.objects.filter(user_id__in=student_ids, status="ACTIVE")
            .select_related("user")
            .order_by("user_id", "-created_at")
        )
        reviewed = set(
            PlanReview.objects.filter(
                reviewer=request.user, plan__user_id__in=student_ids
            ).values_list("plan_id", flat=True)
        )
        for plan in plans:
            row = rows[plan.user_id]
            if row["plan"] is not None:
                continue
            row["plan"] = {
                "id": str(plan.id),
                "title": plan.title,
                "progress": plan.progress,
                "reviewed_by_me": plan.id in reviewed,
                "approved": plan.approved_by_id is not None,
            }

        return Response({"count": len(rows), "results": list(rows.values())})
