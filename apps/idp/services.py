"""Individual Development Plan services.

The plan is a proposal the user owns: generated from real gaps, editable, and
reviewable by a mentor (TZ §13). Nothing here writes a plan the student cannot
change.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.common.enums import Priority
from apps.common.exceptions import Conflict, DomainError

from .models import (
    DevelopmentPlan,
    Goal,
    GoalStatus,
    Milestone,
    MilestoneStatus,
    Origin,
    PlanStatus,
    Task,
    TaskStatus,
    TaskType,
)

# Generated text is stored as a translation key with named arguments after
# "::", not as a sentence. The plan outlives the request that made it and is
# read again in whatever language the person is using that day; a sentence
# built here would be frozen in the language of the moment it was generated.
# Milestone and task titles already worked this way — descriptions, the plan
# title and the summary did not, and showed English inside a Russian page.

#: A 90-day plan split into three monthly milestones (TZ §19).
DEFAULT_MILESTONES = [
    ("plan.milestone.foundation", 30),
    ("plan.milestone.build", 60),
    ("plan.milestone.prove", 90),
]


@transaction.atomic
def create_plan_from_gap(
    *,
    user,
    goal: Goal | None = None,
    profession=None,
    period_days: int = 90,
    source: str = Origin.AI,
    ai_request=None,
) -> DevelopmentPlan:
    """Build a plan from the user's actual skill gap.

    Tasks point at real courses, tests and vacancies via `ref_type`/`ref_id`,
    so completing the underlying object closes the task automatically.
    """
    profile = getattr(user, "student_profile", None)
    profession = profession or getattr(goal, "target_profession", None) or getattr(
        profile, "target_profession", None
    )
    if profession is None:
        raise DomainError(
            "Choose a target profession before generating a plan.",
            code="no_target_profession",
        )

    # Only one ACTIVE plan at a time — the DB constraint enforces it, we
    # archive politely rather than letting the insert blow up.
    DevelopmentPlan.objects.filter(user=user, status=PlanStatus.ACTIVE).update(
        status=PlanStatus.ARCHIVED
    )

    start = timezone.localdate()
    plan = DevelopmentPlan.objects.create(
        user=user,
        goal=goal,
        title=f"plan.generated.title::days={period_days}::profession={profession.name}",
        summary="",
        period_days=period_days,
        start_date=start,
        end_date=start + timedelta(days=period_days),
        status=PlanStatus.ACTIVE,
        source=source,
        ai_request=ai_request,
    )

    milestones = [
        Milestone.objects.create(
            plan=plan,
            title=key,
            due_date=start + timedelta(days=offset),
            order=index,
        )
        for index, (key, offset) in enumerate(DEFAULT_MILESTONES)
        if offset <= period_days
    ]

    tasks = _build_tasks(user, profession, plan, milestones)
    plan.summary = (
        f"plan.generated.summary::tasks={len(tasks)}"
        f"::milestones={len(milestones)}::profession={profession.name}"
    )
    plan.save(update_fields=["summary", "updated_at"])

    from apps.analytics.services import track
    from apps.notifications.services import notify

    track(user, "plan_generated", {"plan_id": str(plan.id), "tasks": len(tasks)})
    notify(
        user=user,
        type="PLAN_READY",
        title_key="notifications.plan.ready.title",
        body_key="notifications.plan.ready.body",
        payload={"title": plan.title, "tasks": len(tasks)},
        ref_type="DevelopmentPlan",
        ref_id=plan.id,
        action_url="/student/plan",
    )
    return plan


def _build_tasks(user, profession, plan, milestones) -> list[Task]:
    """Turn missing skills into concrete, ordered next actions."""
    from apps.learning.models import Course
    from apps.profiles.services import get_skill_gap

    gap = get_skill_gap(user, profession)
    weakest = (gap["missing_skills"] + gap["partial_skills"])[:9]

    tasks: list[Task] = []
    order = 0

    profile = getattr(user, "student_profile", None)
    if profile is not None and profile.profile_completion < 80:
        tasks.append(
            Task.objects.create(
                user=user,
                plan=plan,
                milestone=milestones[0] if milestones else None,
                title="plan.task.complete_profile",
                type=TaskType.PROFILE,
                priority=Priority.HIGH,
                due_date=plan.start_date + timedelta(days=3),
                estimated_minutes=20,
                order=order,
                source=plan.source,
            )
        )
        order += 1

    for index, entry in enumerate(weakest):
        milestone = milestones[min(index // 3, len(milestones) - 1)] if milestones else None
        due = plan.start_date + timedelta(days=7 * (index + 1))

        course = (
            Course.objects.filter(
                skill_links__skill_id=entry["skill_id"], status="PUBLISHED"
            )
            .order_by("-rating_avg", "-enrollment_count")
            .first()
        )

        if course is not None:
            task = Task.objects.create(
                user=user,
                plan=plan,
                milestone=milestone,
                title=course.title,
                description=f"plan.desc.close_gap::skill={entry['skill']}",
                type=TaskType.COURSE,
                ref_type="Course",
                ref_id=course.id,
                priority=Priority.HIGH if entry["requirement"] == "REQUIRED" else Priority.MEDIUM,
                due_date=due,
                estimated_minutes=course.duration_minutes or 120,
                order=order,
                source=plan.source,
            )
        else:
            task = Task.objects.create(
                user=user,
                plan=plan,
                milestone=milestone,
                title=f"plan.task.learn_skill::{entry['skill']}",
                description=(
                    f"plan.desc.reach_level::skill={entry['skill']}"
                    f"::level={entry['required_level']}"
                ),
                type=TaskType.CUSTOM,
                priority=Priority.MEDIUM,
                due_date=due,
                estimated_minutes=180,
                order=order,
                source=plan.source,
            )
        task.related_skills.add(entry["skill_id"])
        tasks.append(task)
        order += 1

    # Finish with something that produces evidence, not just consumption.
    if milestones:
        tasks.append(
            Task.objects.create(
                user=user,
                plan=plan,
                milestone=milestones[-1],
                title="plan.task.build_portfolio_project",
                description="plan.desc.portfolio",
                type=TaskType.PROJECT,
                priority=Priority.HIGH,
                due_date=plan.end_date,
                estimated_minutes=600,
                order=order,
                source=plan.source,
            )
        )
    return tasks


def recalculate_plan_progress(plan: DevelopmentPlan) -> DevelopmentPlan:
    total = plan.tasks.count()
    done = plan.tasks.filter(status=TaskStatus.DONE).count()
    plan.progress = round(100 * done / total) if total else 0

    if total and done == total and plan.status == PlanStatus.ACTIVE:
        plan.status = PlanStatus.COMPLETED
    plan.save(update_fields=["progress", "status", "updated_at"])

    for milestone in plan.milestones.all():
        _recalculate_milestone(milestone)

    if plan.goal_id:
        _recalculate_goal(plan.goal)
    return plan


def _recalculate_milestone(milestone: Milestone) -> None:
    total = milestone.tasks.count()
    done = milestone.tasks.filter(status=TaskStatus.DONE).count()
    milestone.progress = round(100 * done / total) if total else 0

    if total and done == total:
        milestone.status = MilestoneStatus.DONE
    elif milestone.due_date and milestone.due_date < timezone.localdate() and done < total:
        milestone.status = MilestoneStatus.MISSED
    elif done:
        milestone.status = MilestoneStatus.IN_PROGRESS
    milestone.save(update_fields=["progress", "status", "updated_at"])


def _recalculate_goal(goal: Goal) -> None:
    plans = list(goal.plans.all())
    if not plans:
        return
    goal.progress = round(sum(p.progress for p in plans) / len(plans))
    if goal.progress >= 100:
        goal.status = GoalStatus.ACHIEVED
    goal.save(update_fields=["progress", "status", "updated_at"])


@transaction.atomic
def complete_task(task: Task, *, actor=None) -> Task:
    # Closing a plan task is the other kind of real work that counts.
    from .streaks import record_activity

    record_activity(task.user if hasattr(task, "user") else actor)

    if task.status == TaskStatus.DONE:
        return task
    task.status = TaskStatus.DONE
    task.completed_at = timezone.now()
    task.save(update_fields=["status", "completed_at", "updated_at"])

    from apps.analytics.services import track

    track(task.user, "task_completed", {"task_id": str(task.id), "type": task.type})

    if task.plan_id:
        recalculate_plan_progress(task.plan)
    return task


def set_task_status(task: Task, status: str) -> Task:
    if status not in TaskStatus.values:
        raise DomainError("Unknown task status.", code="invalid_status")
    if status == TaskStatus.DONE:
        return complete_task(task)

    task.status = status
    task.completed_at = None
    task.save(update_fields=["status", "completed_at", "updated_at"])
    if task.plan_id:
        recalculate_plan_progress(task.plan)
    return task


def get_today_tasks(user, *, limit: int = 3) -> list[Task]:
    """The "3 tasks for today" widget from the first-demo script (TZ §22.1).

    Overdue first, then due today, then whatever is next — a person opening the
    dashboard should see what to do now, not a backlog.
    """
    today = timezone.localdate()
    open_statuses = [TaskStatus.TODO, TaskStatus.IN_PROGRESS]

    overdue = list(
        Task.objects.filter(
            user=user, status__in=open_statuses, due_date__lt=today
        ).order_by("due_date", "-priority")[:limit]
    )
    if len(overdue) >= limit:
        return overdue

    due_today = list(
        Task.objects.filter(
            user=user, status__in=open_statuses, due_date=today
        ).order_by("-priority")[: limit - len(overdue)]
    )
    selected = overdue + due_today
    if len(selected) >= limit:
        return selected

    upcoming = list(
        Task.objects.filter(user=user, status__in=open_statuses)
        .exclude(id__in=[t.id for t in selected])
        .order_by("due_date", "-priority")[: limit - len(selected)]
    )
    return selected + upcoming


def activate_plan(plan: DevelopmentPlan, *, actor) -> DevelopmentPlan:
    if plan.status == PlanStatus.ACTIVE:
        return plan
    if plan.status in {PlanStatus.COMPLETED, PlanStatus.ARCHIVED}:
        raise Conflict("This plan can no longer be activated.")

    DevelopmentPlan.objects.filter(
        user=plan.user, status=PlanStatus.ACTIVE
    ).exclude(pk=plan.pk).update(status=PlanStatus.ARCHIVED)

    plan.status = PlanStatus.ACTIVE
    plan.save(update_fields=["status", "updated_at"])

    from apps.analytics.services import track

    track(plan.user, "plan_activated", {"plan_id": str(plan.id)})
    return plan
