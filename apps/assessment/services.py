"""Test taking and grading."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.common.enums import EvidenceSource, ModerationStatus
from apps.common.exceptions import Conflict, DomainError, NotAllowed
from apps.common.recompute import schedule_recompute

from .models import (
    AttemptAnswer,
    AttemptStatus,
    Question,
    QuestionType,
    Test,
    TestAttempt,
    TestSkillResult,
)


@transaction.atomic
def start_attempt(user, test: Test) -> TestAttempt:
    if test.status != ModerationStatus.PUBLISHED:
        raise NotAllowed("This test is not available.", code="test_not_published")

    existing = TestAttempt.objects.filter(
        user=user, test=test, status=AttemptStatus.IN_PROGRESS
    ).first()
    if existing is not None:
        if existing.expires_at and existing.expires_at < timezone.now():
            _expire_attempt(existing)
        else:
            return existing

    used = TestAttempt.objects.filter(user=user, test=test).count()
    if test.max_attempts and used >= test.max_attempts:
        raise Conflict(
            "No attempts left for this test.",
            code="attempts_exhausted",
            details={"max_attempts": test.max_attempts},
        )

    attempt = TestAttempt.objects.create(
        user=user,
        test=test,
        attempt_no=used + 1,
        expires_at=(
            timezone.now() + timedelta(minutes=test.time_limit_minutes)
            if test.time_limit_minutes
            else None
        ),
        max_score=test.max_score,
    )

    from apps.analytics.services import track

    track(user, "test_started", {"test_id": str(test.id), "attempt": attempt.attempt_no})
    return attempt


def _expire_attempt(attempt: TestAttempt) -> TestAttempt:
    attempt.status = AttemptStatus.EXPIRED
    attempt.submitted_at = timezone.now()
    attempt.save(update_fields=["status", "submitted_at", "updated_at"])
    return attempt


@transaction.atomic
def submit_attempt(attempt: TestAttempt, answers: list[dict]) -> TestAttempt:
    """Grade a submission.

    `answers` is ``[{"question_id": ..., "option_ids": [...], "text": "..."}]``.
    Grading happens entirely server-side — the client never learns which option
    was correct until the attempt is closed.
    """
    if attempt.status != AttemptStatus.IN_PROGRESS:
        raise Conflict("This attempt is already closed.", code="attempt_closed")

    expired = bool(attempt.expires_at and attempt.expires_at < timezone.now())

    questions = {
        str(q.id): q
        for q in attempt.test.questions.prefetch_related("options").all()
    }
    submitted = {str(a.get("question_id")): a for a in answers}

    total_points = 0
    earned_points = 0
    per_skill: dict[str, dict[str, int]] = {}

    for question_id, question in questions.items():
        total_points += question.points
        answer = submitted.get(question_id)
        is_correct, points = _grade_question(question, answer)
        earned_points += points

        record, _created = AttemptAnswer.objects.update_or_create(
            attempt=attempt,
            question=question,
            defaults={
                "text_answer": (answer or {}).get("text", "")[:500],
                "is_correct": is_correct,
                "points_awarded": points,
            },
        )
        option_ids = (answer or {}).get("option_ids") or []
        if option_ids:
            record.selected_options.set(
                question.options.filter(id__in=option_ids)
            )
        else:
            record.selected_options.clear()

        if question.skill_id:
            bucket = per_skill.setdefault(
                str(question.skill_id), {"total": 0, "correct": 0, "skill": question.skill_id}
            )
            bucket["total"] += 1
            bucket["correct"] += 1 if is_correct else 0

    percentage = round(100 * earned_points / total_points) if total_points else 0

    attempt.score = earned_points
    attempt.max_score = total_points
    attempt.percentage = percentage
    attempt.passed = percentage >= attempt.test.passing_score
    attempt.submitted_at = timezone.now()
    attempt.time_spent_seconds = int(
        (attempt.submitted_at - attempt.started_at).total_seconds()
    )
    attempt.status = AttemptStatus.EXPIRED if expired else AttemptStatus.GRADED
    attempt.save()

    _store_skill_results(attempt, per_skill)

    from apps.analytics.services import track

    track(
        attempt.user,
        "test_submitted",
        {
            "test_id": str(attempt.test_id),
            "percentage": percentage,
            "passed": attempt.passed,
        },
    )

    if not expired:
        _apply_test_results(attempt, per_skill)
    return attempt


def _grade_question(question: Question, answer: dict | None) -> tuple[bool, int]:
    if not answer:
        return False, 0

    if question.type == QuestionType.SHORT_ANSWER:
        given = (answer.get("text") or "").strip().casefold()
        accepted = {str(a).strip().casefold() for a in question.accepted_answers or []}
        correct = bool(given) and given in accepted
        return correct, question.points if correct else 0

    selected = {str(o) for o in (answer.get("option_ids") or [])}
    expected = {str(o.id) for o in question.options.all() if o.is_correct}

    if not expected:
        return False, 0
    if question.type == QuestionType.MULTIPLE:
        correct = selected == expected
    else:
        correct = len(selected) == 1 and selected == expected
    return correct, question.points if correct else 0


def _store_skill_results(attempt: TestAttempt, per_skill: dict) -> None:
    TestSkillResult.objects.filter(attempt=attempt).delete()
    TestSkillResult.objects.bulk_create(
        [
            TestSkillResult(
                attempt=attempt,
                skill_id=data["skill"],
                questions_total=data["total"],
                questions_correct=data["correct"],
                percentage=round(100 * data["correct"] / data["total"])
                if data["total"]
                else 0,
            )
            for data in per_skill.values()
        ]
    )


def _apply_test_results(attempt: TestAttempt, per_skill: dict) -> None:
    """Turn a graded attempt into skill evidence — if the test is allowed to.

    Employer screening tests are excluded here: letting a company's own test
    move a candidate's global score is the conflict of interest documented in
    docs/01-ANALYSIS.md §3.2.
    """
    from apps.learning.services import _complete_linked_tasks
    from apps.notifications.services import notify
    from apps.profiles.services import record_skill_evidence
    from apps.taxonomy.models import Skill

    test = attempt.test
    user = attempt.user

    notify(
        user=user,
        type="TEST_GRADED",
        title_key="notifications.test.graded.title",
        body_key="notifications.test.graded.body",
        payload={
            "title": test.title,
            "percentage": attempt.percentage,
            "passed": attempt.passed,
        },
        ref_type="Test",
        ref_id=test.id,
        action_url=f"/student/tests/{test.id}/results",
    )
    _complete_linked_tasks(user, ref_type="Test", ref_id=test.id)

    if not test.feeds_knowledge_profile:
        return

    touched = []
    if per_skill:
        skills = {str(s.id): s for s in Skill.objects.filter(id__in=per_skill.keys())}
        for skill_id, data in per_skill.items():
            skill = skills.get(skill_id)
            if skill is None:
                continue
            score = round(100 * data["correct"] / data["total"]) if data["total"] else 0
            record_skill_evidence(
                user=user,
                skill=skill,
                source=EvidenceSource.TEST,
                score=score,
                ref_type="Test",
                ref_id=test.id,
                note=f"Test: {test.title}"[:255],
            )
            touched.append(skill)
    else:
        # Test declares its skills at the test level rather than per question.
        for link in test.skill_links.select_related("skill"):
            record_skill_evidence(
                user=user,
                skill=link.skill,
                source=EvidenceSource.TEST,
                score=attempt.percentage,
                ref_type="Test",
                ref_id=test.id,
                note=f"Test: {test.title}"[:255],
            )
            touched.append(link.skill)

    if touched:
        schedule_recompute(user, skills=touched, reason="test_graded")


def moderate_test(test: Test, *, approve: bool, actor, note: str = "") -> Test:
    if test.status != ModerationStatus.PENDING_REVIEW:
        raise Conflict("Only tests pending review can be moderated.")
    if approve and not test.questions.exists():
        raise DomainError("A test needs at least one question.", code="test_empty")

    test.status = ModerationStatus.PUBLISHED if approve else ModerationStatus.REJECTED
    test.moderation_note = note
    test.moderated_by = actor
    if approve:
        test.published_at = timezone.now()
    test.save(
        update_fields=[
            "status",
            "moderation_note",
            "moderated_by",
            "published_at",
            "updated_at",
        ]
    )

    from apps.audit.services import log_moderation

    log_moderation(test, decision=test.status, actor=actor, note=note)
    return test


def get_test_analytics(test: Test) -> dict:
    """Employer view of a test's results (prompt §16)."""
    attempts = TestAttempt.objects.filter(
        test=test, status__in=[AttemptStatus.GRADED, AttemptStatus.SUBMITTED]
    )
    scores = list(attempts.values_list("percentage", flat=True))
    return {
        "attempts": len(scores),
        "participants": attempts.values("user").distinct().count(),
        "passed": attempts.filter(passed=True).count(),
        "pass_rate": round(100 * attempts.filter(passed=True).count() / len(scores))
        if scores
        else 0,
        "average_score": round(sum(scores) / len(scores)) if scores else 0,
        "highest_score": max(scores) if scores else 0,
        "lowest_score": min(scores) if scores else 0,
    }
