"""Learning services: enrolment, progress, completion, moderation."""

from __future__ import annotations

import secrets

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify

from apps.common.enums import EvidenceSource, ModerationStatus
from apps.common.exceptions import Conflict, DomainError, NotAllowed
from apps.common.recompute import schedule_recompute

from .models import (
    Certificate,
    Course,
    Enrollment,
    EnrollmentStatus,
    Lesson,
    LessonProgress,
    LessonProgressStatus,
)


def generate_course_slug(title: str) -> str:
    base = slugify(title)[:140] or "course"
    slug, counter = base, 1
    while Course.objects.filter(slug=slug).exists():
        counter += 1
        slug = f"{base}-{counter}"[:160]
    return slug


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------
def submit_for_review(course: Course, *, actor) -> Course:
    if course.status not in {ModerationStatus.DRAFT, ModerationStatus.REJECTED}:
        raise Conflict("Only draft or rejected courses can be submitted for review.")
    if not course.modules.exists():
        raise DomainError(
            "Add at least one module with a lesson before submitting.",
            code="course_empty",
        )
    course.status = ModerationStatus.PENDING_REVIEW
    course.save(update_fields=["status", "updated_at"])

    from apps.audit.services import log_status_change

    log_status_change(
        course, from_status=ModerationStatus.DRAFT, to_status=course.status, actor=actor
    )
    return course


def moderate_course(course: Course, *, approve: bool, actor, note: str = "") -> Course:
    if course.status != ModerationStatus.PENDING_REVIEW:
        raise Conflict("Only courses pending review can be moderated.")

    course.status = (
        ModerationStatus.PUBLISHED if approve else ModerationStatus.REJECTED
    )
    course.moderation_note = note
    course.moderated_by = actor
    if approve:
        course.published_at = timezone.now()
    course.save(
        update_fields=[
            "status",
            "moderation_note",
            "moderated_by",
            "published_at",
            "updated_at",
        ]
    )

    from apps.audit.services import log_moderation
    from apps.notifications.services import notify

    log_moderation(
        course, decision=course.status, actor=actor, note=note
    )
    notify(
        user=course.author,
        type="MODERATION_RESULT",
        title_key="notifications.moderation.course.title",
        body_key="notifications.moderation.course.body",
        payload={"title": course.title, "approved": approve, "note": note},
        ref_type="Course",
        ref_id=course.id,
        action_url=f"/employer/courses/{course.id}",
    )
    return course


# ---------------------------------------------------------------------------
# Enrolment and progress
# ---------------------------------------------------------------------------
def can_open_course(user, course: Course) -> bool:
    """May this person read the inside of the course?

    Enrolment, authorship, the owning company, or admin. Used by the note
    endpoints as well as the lesson reader, so the two cannot drift into
    disagreeing about who is allowed where.

    The company is here because editing already allowed it: a colleague at the
    company that owns a course could change its lessons but not read them,
    which meant an employer opening their own course to check it saw a locked
    list. Ownership is the company's; authorship is only who typed it first.
    """
    if user.is_admin or course.author_id == user.id:
        return True

    company = getattr(user, "employer_profile", None)
    # `employer_id` is None for platform courses, and a company id never is,
    # so this cannot accidentally match one.
    if company is not None and course.employer_id == company.id:
        return True

    return Enrollment.objects.filter(user=user, course=course).exists()


def can_open_lesson(user, lesson: Lesson) -> bool:
    """As above, plus the free preview a course offers to everyone."""
    return lesson.is_free_preview or can_open_course(user, lesson.module.course)


# ---------------------------------------------------------------------------
@transaction.atomic
def enroll(user, course: Course) -> Enrollment:
    if not course.is_published:
        raise NotAllowed("This course is not available.", code="course_not_published")

    enrollment, created = Enrollment.objects.get_or_create(user=user, course=course)
    if not created:
        return enrollment

    course.enrollment_count = course.enrollments.count()
    course.save(update_fields=["enrollment_count", "updated_at"])

    from apps.analytics.services import track

    track(user, "course_enrolled", {"course_id": str(course.id), "title": course.title})
    return enrollment


@transaction.atomic
def complete_lesson(user, lesson: Lesson, *, seconds_spent: int = 0) -> Enrollment:
    """Mark a lesson done and cascade the consequences."""
    # A finished lesson is real work, so it counts toward today's streak.
    from apps.idp.streaks import record_activity

    record_activity(user)

    course = lesson.module.course
    enrollment = Enrollment.objects.filter(user=user, course=course).first()
    if enrollment is None:
        raise NotAllowed("Enrol in the course first.", code="not_enrolled")

    progress, _created = LessonProgress.objects.get_or_create(
        enrollment=enrollment, lesson=lesson
    )
    if progress.status != LessonProgressStatus.COMPLETED:
        progress.status = LessonProgressStatus.COMPLETED
        progress.completed_at = timezone.now()
    progress.seconds_spent += max(0, seconds_spent)
    progress.save(
        update_fields=["status", "completed_at", "seconds_spent", "updated_at"]
    )

    from apps.analytics.services import track

    track(user, "lesson_completed", {"lesson_id": str(lesson.id)})
    return _recalculate_enrollment(enrollment)


def _recalculate_enrollment(enrollment: Enrollment) -> Enrollment:
    total = Lesson.objects.filter(module__course=enrollment.course).count()
    done = enrollment.lesson_progress.filter(
        status=LessonProgressStatus.COMPLETED
    ).count()

    progress = round(100 * done / total) if total else 0
    was_completed = enrollment.status == EnrollmentStatus.COMPLETED

    enrollment.progress = progress
    enrollment.last_activity_at = timezone.now()
    if enrollment.started_at is None and done:
        enrollment.started_at = timezone.now()

    if total and done >= total:
        enrollment.status = EnrollmentStatus.COMPLETED
        enrollment.completed_at = enrollment.completed_at or timezone.now()
    elif done:
        enrollment.status = EnrollmentStatus.IN_PROGRESS

    enrollment.save(
        update_fields=[
            "progress",
            "status",
            "started_at",
            "completed_at",
            "last_activity_at",
            "updated_at",
        ]
    )

    if enrollment.status == EnrollmentStatus.COMPLETED and not was_completed:
        _on_course_completed(enrollment)
    return enrollment


def _on_course_completed(enrollment: Enrollment) -> None:
    """The ripple described in prompt §32."""
    from apps.analytics.services import track
    from apps.notifications.services import notify
    from apps.profiles.services import record_skill_evidence

    course = enrollment.course
    user = enrollment.user

    course.completion_count = course.enrollments.filter(
        status=EnrollmentStatus.COMPLETED
    ).count()
    course.save(update_fields=["completion_count", "updated_at"])

    touched_skills = []
    for link in course.skill_links.select_related("skill"):
        record_skill_evidence(
            user=user,
            skill=link.skill,
            source=EvidenceSource.COURSE,
            score=link.target_proficiency,
            ref_type="Course",
            ref_id=course.id,
            note=f"Completed course: {course.title}"[:255],
        )
        touched_skills.append(link.skill)

    if course.is_certified:
        issue_certificate(user, course)

    track(user, "course_completed", {"course_id": str(course.id), "title": course.title})
    notify(
        user=user,
        type="COURSE_COMPLETED",
        title_key="notifications.course.completed.title",
        body_key="notifications.course.completed.body",
        payload={"title": course.title},
        ref_type="Course",
        ref_id=course.id,
        action_url=f"/student/courses/{course.id}",
    )

    _complete_linked_tasks(user, ref_type="Course", ref_id=course.id)
    schedule_recompute(user, skills=touched_skills, reason="course_completed")


def _complete_linked_tasks(user, *, ref_type: str, ref_id) -> None:
    """Close IDP tasks that pointed at whatever just happened.

    Without this the plan would show "Complete SQL Fundamentals" as pending
    even though the course is finished — the exact disconnect prompt §32 warns
    against.
    """
    from apps.idp.models import Task, TaskStatus

    Task.objects.filter(
        user=user,
        ref_type=ref_type,
        ref_id=ref_id,
        status__in=[TaskStatus.TODO, TaskStatus.IN_PROGRESS],
    ).update(status=TaskStatus.DONE, completed_at=timezone.now())


def issue_certificate(user, course: Course) -> Certificate:
    certificate = Certificate.objects.filter(user=user, course=course).first()
    if certificate is not None:
        return certificate
    return Certificate.objects.create(
        user=user,
        course=course,
        serial=f"YK-C-{timezone.now():%Y}-{secrets.token_hex(4).upper()}",
        verification_code=secrets.token_urlsafe(16)[:32],
    )


def get_course_analytics(course: Course) -> dict:
    """Employer-facing course performance (prompt §15)."""
    from apps.assessment.models import TestAttempt

    stats = course.enrollments.aggregate(
        enrolled=Count("id"),
        completed=Count("id", filter=Q(status=EnrollmentStatus.COMPLETED)),
        in_progress=Count("id", filter=Q(status=EnrollmentStatus.IN_PROGRESS)),
    )
    attempts = TestAttempt.objects.filter(test__course=course, passed=True)
    scores = list(attempts.values_list("percentage", flat=True))

    return {
        "enrolled": stats["enrolled"] or 0,
        "completed": stats["completed"] or 0,
        "in_progress": stats["in_progress"] or 0,
        "completion_rate": course.completion_rate,
        "average_test_score": round(sum(scores) / len(scores)) if scores else 0,
        "tests_passed": len(scores),
    }
