"""The `/api/v1/me/` namespace — everything about the signed-in user."""

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.enums import Role
from apps.common.exceptions import DomainError, NotAllowed
from apps.common.permissions import IsStudent
from apps.common.recompute import schedule_recompute
from apps.taxonomy.models import Profession, Skill

from ..models import Education, UserSkill
from ..services import (
    declare_skill,
    generate_employer_slug,
    recalculate_profile_completion,
    remove_declared_skill,
)
from .serializers import (
    DeclareSkillSerializer,
    EducationSerializer,
    EmployerProfileSerializer,
    OnboardingSerializer,
    StudentProfileSerializer,
    UserSkillDetailSerializer,
    UserSkillSerializer,
)


@extend_schema(tags=["profile"])
class MyProfileView(APIView):
    """GET/PATCH the profile matching the caller's role."""

    permission_classes = [IsAuthenticated]

    def _resolve(self, user):
        if user.role == Role.STUDENT:
            return getattr(user, "student_profile", None), StudentProfileSerializer
        if user.role == Role.EMPLOYER:
            return getattr(user, "employer_profile", None), EmployerProfileSerializer
        return None, None

    def get(self, request):
        profile, serializer_class = self._resolve(request.user)
        if profile is None:
            raise DomainError("No profile for this role.", code="no_profile")
        return Response(serializer_class(profile).data)

    def patch(self, request):
        profile, serializer_class = self._resolve(request.user)
        if profile is None:
            raise DomainError("No profile for this role.", code="no_profile")

        serializer = serializer_class(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()

        if request.user.role == Role.EMPLOYER and not instance.slug:
            instance.slug = generate_employer_slug(instance.display_name)
            instance.save(update_fields=["slug"])

        if request.user.role == Role.STUDENT:
            recalculate_profile_completion(instance)
            if "target_profession" in request.data:
                from apps.analytics.services import track
                from apps.matching.services import recompute_profession_matches

                recompute_profession_matches(request.user)
                track(
                    request.user,
                    "profession_selected",
                    {"profession_id": str(instance.target_profession_id)},
                )

        return Response(serializer_class(instance).data)


@extend_schema(tags=["profile"])
class OnboardingView(APIView):
    """Complete onboarding in a single round trip (prompt §26)."""

    permission_classes = [IsAuthenticated, IsStudent]

    @extend_schema(request=OnboardingSerializer, responses={200: StudentProfileSerializer})
    @transaction.atomic
    def post(self, request):
        serializer = OnboardingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            raise DomainError("Student profile missing.", code="no_profile")

        for field in (
            "first_name",
            "last_name",
            "city",
            "education_status",
            "institution",
            "employment_status",
            "languages",
        ):
            if field in data:
                setattr(profile, field, data[field])

        if data.get("region"):
            profile.region_id = data["region"]
        if data.get("target_profession"):
            profile.target_profession_id = data["target_profession"]

        profile.onboarding_completed_at = profile.onboarding_completed_at or timezone.now()
        profile.save()

        declared = data.get("skills") or []
        if declared:
            skills = {
                str(s.id): s
                for s in Skill.objects.filter(
                    id__in=[entry["skill"] for entry in declared]
                )
            }
            for entry in declared:
                skill = skills.get(str(entry["skill"]))
                if skill is not None:
                    declare_skill(
                        user=request.user, skill=skill, proficiency=entry["proficiency"]
                    )

        recalculate_profile_completion(profile)
        schedule_recompute(request.user, reason="onboarding")

        from apps.analytics.services import track

        track(request.user, "onboarding_completed", {"skills": len(declared)})
        return Response(StudentProfileSerializer(profile).data)


@extend_schema(tags=["profile"])
class MySkillsViewSet(viewsets.ModelViewSet):
    """The caller's own skills. Never anybody else's."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserSkillSerializer
    filterset_fields = ["status", "skill__category"]
    ordering_fields = ["proficiency", "last_evidence_at"]

    def get_queryset(self):
        return (
            UserSkill.objects.filter(user=self.request.user)
            .select_related("skill", "skill__category")
            .order_by("-proficiency")
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return UserSkillDetailSerializer
        return UserSkillSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        from apps.knowledge.models import KnowledgeScore

        context["knowledge_by_skill"] = dict(
            KnowledgeScore.objects.filter(user=self.request.user).values_list(
                "skill_id", "score"
            )
        )
        return context

    @extend_schema(request=DeclareSkillSerializer, responses={201: UserSkillSerializer})
    def create(self, request, *args, **kwargs):
        serializer = DeclareSkillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        skill = Skill.objects.filter(
            id=serializer.validated_data["skill"], is_active=True
        ).first()
        if skill is None:
            raise DomainError("Unknown skill.", code="unknown_skill")

        user_skill = declare_skill(
            user=request.user,
            skill=skill,
            proficiency=serializer.validated_data["proficiency"],
        )
        schedule_recompute(request.user, skills=[skill], reason="skill_declared")
        return Response(
            UserSkillSerializer(user_skill).data, status=status.HTTP_201_CREATED
        )

    def destroy(self, request, *args, **kwargs):
        user_skill = self.get_object()
        skill = user_skill.skill
        remove_declared_skill(user=request.user, skill=skill)
        schedule_recompute(request.user, skills=[skill], reason="skill_removed")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses={200: dict})
    @action(detail=False, methods=["get"], url_path="gap")
    def gap(self, request):
        """Skill gap against a profession (defaults to the declared target)."""
        from ..services import get_skill_gap

        profession_id = request.query_params.get("profession")
        profile = getattr(request.user, "student_profile", None)
        profession_id = profession_id or getattr(profile, "target_profession_id", None)
        if not profession_id:
            raise DomainError(
                "Select a target profession first.", code="no_target_profession"
            )

        profession = Profession.objects.filter(id=profession_id).first()
        if profession is None:
            raise DomainError("Unknown profession.", code="unknown_profession")
        return Response(get_skill_gap(request.user, profession))


@extend_schema(tags=["profile"])
class MyEducationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = EducationSerializer

    def get_queryset(self):
        return Education.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.save()
        profile = getattr(self.request.user, "student_profile", None)
        if profile is not None:
            recalculate_profile_completion(profile)
        return instance


@extend_schema(tags=["profile"])
class StudentDashboardView(APIView):
    """Everything the student dashboard needs, in one request (prompt §4)."""

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request):
        from apps.ai.services import get_recommendations
        from apps.capital.services import get_capital_overview
        from apps.idp.models import DevelopmentPlan, PlanStatus, Task, TaskStatus
        from apps.idp.services import get_today_tasks
        from apps.jobs.models import Application
        from apps.knowledge.services import get_knowledge_overview
        from apps.learning.models import Enrollment, EnrollmentStatus
        from apps.matching.models import MatchResult
        from apps.notifications.services import unread_count
        from apps.profiles.services import touch_activity

        user = request.user
        touch_activity(user)
        profile = getattr(user, "student_profile", None)

        plan = DevelopmentPlan.objects.filter(user=user, status=PlanStatus.ACTIVE).first()
        today_tasks = get_today_tasks(user, limit=3)

        enrollments = Enrollment.objects.filter(user=user)
        completed_courses = enrollments.filter(status=EnrollmentStatus.COMPLETED).count()
        skills = UserSkill.objects.filter(user=user)
        verified_skills = skills.filter(status="VERIFIED").count()

        knowledge = get_knowledge_overview(user, limit=8)
        capital = get_capital_overview(user)

        strong_matches = MatchResult.objects.filter(
            student=user, overall_score__gte=60
        ).count()

        # Career progress blends the measurable parts of the journey rather
        # than inventing a number: profile, capital, plan execution.
        career_progress = round(
            0.2 * (profile.profile_completion if profile else 0)
            + 0.4 * capital["overall"]
            + 0.4 * (plan.progress if plan else 0)
        )

        # Whether the intake interview has been answered. Read-only on purpose:
        # the dashboard must not create a session as a side effect of being
        # looked at, or every visitor would leave an empty interview behind.
        from apps.ai.models import IntakeSession, IntakeStatus
        from apps.idp.company import overview as company_overview
        from apps.idp.streaks import overview as streak_overview

        intake_completed = IntakeSession.objects.filter(
            user=user, status=IntakeStatus.COMPLETED
        ).exists()
        intake_started = IntakeSession.objects.filter(
            user=user, status=IntakeStatus.IN_PROGRESS
        ).exists()

        return Response(
            {
                "greeting_name": profile.first_name if profile else "",
                "youth_id": profile.youth_id if profile else None,
                "intake_completed": intake_completed,
                "intake_started": intake_started,
                # Two answers to "why come back tomorrow" and "am I alone".
                "streak": streak_overview(user),
                "company": company_overview(user),
                "target_profession": {
                    "id": str(profile.target_profession_id),
                    "name": profile.target_profession.name,
                }
                if profile and profile.target_profession_id
                else None,
                "career_progress": career_progress,
                "stats": {
                    "profile_completion": profile.profile_completion if profile else 0,
                    "skills_total": skills.count(),
                    "skills_verified": verified_skills,
                    "courses_enrolled": enrollments.count(),
                    "courses_completed": completed_courses,
                    "tests_passed": user.test_attempts.filter(passed=True).count(),
                    "applications": Application.objects.filter(student=user).count(),
                    "matching_vacancies": strong_matches,
                    "unread_notifications": unread_count(user),
                },
                "capital": capital,
                "knowledge": knowledge,
                "plan": {
                    "id": str(plan.id),
                    "title": plan.title,
                    "progress": plan.progress,
                    "days_remaining": plan.days_remaining,
                    "tasks_total": plan.tasks.count(),
                    "tasks_done": plan.tasks.filter(status=TaskStatus.DONE).count(),
                }
                if plan
                else None,
                "today_tasks": [
                    {
                        "id": str(task.id),
                        "title": task.title,
                        "type": task.type,
                        "priority": task.priority,
                        "due_date": task.due_date,
                        "is_overdue": task.is_overdue,
                        "estimated_minutes": task.estimated_minutes,
                        "ref_type": task.ref_type,
                        "ref_id": str(task.ref_id) if task.ref_id else None,
                    }
                    for task in today_tasks
                ],
                "overdue_tasks": Task.objects.filter(
                    user=user,
                    status__in=[TaskStatus.TODO, TaskStatus.IN_PROGRESS],
                    due_date__lt=timezone.localdate(),
                ).count(),
                "recommendations": [
                    {
                        "id": str(rec.id),
                        "type": rec.type,
                        "title": rec.title,
                        "score": rec.score,
                        "reason_code": rec.reason_code,
                        "reason_data": rec.reason_data,
                        "ref_id": str(rec.ref_id) if rec.ref_id else None,
                    }
                    for rec in get_recommendations(user, limit=6)
                ],
            }
        )


@extend_schema(tags=["profile"])
class PublicProfileView(APIView):
    """Another user's profile, filtered by what the viewer is allowed to see."""

    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        from apps.accounts.models import User
        from apps.audit.models import AuditAction, AuditSeverity
        from apps.audit.services import log_action

        from ..services import can_view_student_profile
        from .serializers import PublicStudentCardSerializer

        target = User.objects.filter(id=user_id, role=Role.STUDENT).first()
        if target is None:
            raise NotAllowed("Profile not available.", code="not_found")

        profile = getattr(target, "student_profile", None)
        if profile is None:
            raise NotAllowed("Profile not available.", code="not_found")

        if not can_view_student_profile(viewer=request.user, student_user=target):
            return Response(
                {
                    "identified": False,
                    "card": PublicStudentCardSerializer(profile).data,
                }
            )

        # Seeing an identified profile is a personal-data access — record it.
        log_action(
            action=AuditAction.PII_ACCESS,
            obj=profile,
            actor=request.user,
            note=f"viewed by {request.user.role}",
            severity=AuditSeverity.NOTICE,
        )
        return Response(
            {"identified": True, "profile": StudentProfileSerializer(profile).data}
        )
