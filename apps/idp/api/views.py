"""IDP endpoints — goals, plans, tasks."""

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.exceptions import DomainError
from apps.common.permissions import IsStudent
from apps.taxonomy.models import Profession

from ..models import DevelopmentPlan, Goal, Task
from ..services import (
    activate_plan,
    create_plan_from_gap,
    get_today_tasks,
    set_task_status,
)
from .serializers import (
    DevelopmentPlanDetailSerializer,
    DevelopmentPlanListSerializer,
    GeneratePlanSerializer,
    GoalSerializer,
    TaskSerializer,
)


@extend_schema(tags=["idp"])
class GoalViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsStudent]
    serializer_class = GoalSerializer
    filterset_fields = ["horizon", "status"]

    def get_queryset(self):
        return (
            Goal.objects.filter(user=self.request.user)
            .prefetch_related("plans")
            .order_by("horizon", "-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, source="USER")


@extend_schema(tags=["idp"])
class DevelopmentPlanViewSet(viewsets.ReadOnlyModelViewSet):
    """Plans are generated or activated, never created by raw POST — the
    generator is what makes them grounded in a real skill gap."""

    permission_classes = [IsAuthenticated, IsStudent]
    filterset_fields = ["status"]

    def get_queryset(self):
        return (
            DevelopmentPlan.objects.filter(user=self.request.user)
            .prefetch_related("milestones__tasks__related_skills", "reviews__reviewer")
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return DevelopmentPlanDetailSerializer
        return DevelopmentPlanListSerializer

    @extend_schema(
        request=GeneratePlanSerializer, responses={201: DevelopmentPlanDetailSerializer}
    )
    @action(detail=False, methods=["post"])
    def generate(self, request):
        """Generate the 90-day plan (TZ §19, §22.1)."""
        serializer = GeneratePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        goal = None
        if data.get("goal"):
            goal = Goal.objects.filter(id=data["goal"], user=request.user).first()
            if goal is None:
                raise DomainError("Unknown goal.", code="unknown_goal")

        profession = None
        if data.get("profession"):
            profession = Profession.objects.filter(id=data["profession"]).first()

        plan = create_plan_from_gap(
            user=request.user,
            goal=goal,
            profession=profession,
            period_days=data.get("period_days", 90),
        )
        return Response(
            DevelopmentPlanDetailSerializer(plan).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(responses={200: DevelopmentPlanDetailSerializer})
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        plan = activate_plan(self.get_object(), actor=request.user)
        return Response(DevelopmentPlanDetailSerializer(plan).data)

    @extend_schema(responses={200: DevelopmentPlanDetailSerializer})
    @action(detail=False, methods=["get"])
    def active(self, request):
        plan = self.get_queryset().filter(status="ACTIVE").first()
        if plan is None:
            return Response(None)
        return Response(DevelopmentPlanDetailSerializer(plan).data)


@extend_schema(tags=["idp"])
class TaskViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsStudent]
    serializer_class = TaskSerializer
    filterset_fields = ["status", "type", "priority", "plan", "milestone"]
    ordering_fields = ["due_date", "priority", "created_at"]

    def get_queryset(self):
        return (
            Task.objects.filter(user=self.request.user)
            .prefetch_related("related_skills")
            .order_by("due_date", "order")
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, source="USER")

    @extend_schema(request=dict, responses={200: TaskSerializer})
    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, pk=None):
        task = set_task_status(self.get_object(), request.data.get("status", "DONE"))
        return Response(TaskSerializer(task).data)

    @extend_schema(responses={200: TaskSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def today(self, request):
        """The "3 tasks for today" widget (TZ §22.1)."""
        limit = min(int(request.query_params.get("limit", 3)), 10)
        return Response(
            TaskSerializer(get_today_tasks(request.user, limit=limit), many=True).data
        )


@extend_schema(tags=["idp"])
class PlanSummaryView(APIView):
    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request):
        from django.utils import timezone

        from ..models import PlanStatus, TaskStatus

        plan = DevelopmentPlan.objects.filter(
            user=request.user, status=PlanStatus.ACTIVE
        ).first()
        tasks = Task.objects.filter(user=request.user)
        open_statuses = [TaskStatus.TODO, TaskStatus.IN_PROGRESS]

        return Response(
            {
                "has_active_plan": plan is not None,
                "plan": DevelopmentPlanListSerializer(plan).data if plan else None,
                "tasks_open": tasks.filter(status__in=open_statuses).count(),
                "tasks_done": tasks.filter(status=TaskStatus.DONE).count(),
                "tasks_overdue": tasks.filter(
                    status__in=open_statuses, due_date__lt=timezone.localdate()
                ).count(),
            }
        )
