"""IDP serializers."""

from rest_framework import serializers

from ..models import DevelopmentPlan, Goal, Milestone, Task


class TaskSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)
    skills = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "plan",
            "milestone",
            "title",
            "description",
            "type",
            "ref_type",
            "ref_id",
            "priority",
            "due_date",
            "status",
            "completed_at",
            "estimated_minutes",
            "order",
            "source",
            "is_overdue",
            "skills",
        ]
        read_only_fields = ["id", "completed_at", "source", "plan", "milestone"]

    def get_skills(self, task) -> list[dict]:
        return [{"id": str(s.id), "name": s.name} for s in task.related_skills.all()]


class MilestoneSerializer(serializers.ModelSerializer):
    tasks = TaskSerializer(many=True, read_only=True)

    class Meta:
        model = Milestone
        fields = [
            "id",
            "title",
            "description",
            "due_date",
            "order",
            "status",
            "progress",
            "tasks",
        ]
        read_only_fields = ["id", "status", "progress"]


class DevelopmentPlanListSerializer(serializers.ModelSerializer):
    days_remaining = serializers.IntegerField(read_only=True)
    tasks_total = serializers.SerializerMethodField()
    tasks_done = serializers.SerializerMethodField()

    class Meta:
        model = DevelopmentPlan
        fields = [
            "id",
            "title",
            "summary",
            "goal",
            "period_days",
            "start_date",
            "end_date",
            "status",
            "source",
            "progress",
            "days_remaining",
            "tasks_total",
            "tasks_done",
            "created_at",
        ]
        read_only_fields = fields

    def get_tasks_total(self, plan) -> int:
        return plan.tasks.count()

    def get_tasks_done(self, plan) -> int:
        return plan.tasks.filter(status="DONE").count()


class DevelopmentPlanDetailSerializer(DevelopmentPlanListSerializer):
    milestones = MilestoneSerializer(many=True, read_only=True)

    class Meta(DevelopmentPlanListSerializer.Meta):
        fields = [*DevelopmentPlanListSerializer.Meta.fields, "milestones"]
        read_only_fields = fields

class GoalSerializer(serializers.ModelSerializer):
    plans = DevelopmentPlanListSerializer(many=True, read_only=True)

    class Meta:
        model = Goal
        fields = [
            "id",
            "title",
            "description",
            "horizon",
            "target_profession",
            "target_date",
            "status",
            "progress",
            "source",
            "plans",
            "created_at",
        ]
        read_only_fields = ["id", "progress", "source", "created_at"]


class GeneratePlanSerializer(serializers.Serializer):
    goal = serializers.UUIDField(required=False, allow_null=True)
    profession = serializers.UUIDField(required=False, allow_null=True)
    period_days = serializers.IntegerField(default=90, min_value=14, max_value=365)
