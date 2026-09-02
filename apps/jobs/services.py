"""Vacancy, application and placement services."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.common.enums import ModerationStatus
from apps.common.exceptions import Conflict, DomainError, NotAllowed

from .models import (
    ALLOWED_TRANSITIONS,
    Application,
    ApplicationEvent,
    ApplicationStatus,
    Placement,
    PlacementStatus,
    Vacancy,
)


# ---------------------------------------------------------------------------
# Vacancy lifecycle
# ---------------------------------------------------------------------------
def submit_vacancy_for_review(vacancy: Vacancy, *, actor) -> Vacancy:
    if vacancy.status not in {ModerationStatus.DRAFT, ModerationStatus.REJECTED}:
        raise Conflict("Only draft or rejected vacancies can be submitted.")
    if not vacancy.skill_links.exists():
        raise DomainError(
            "Add at least one required skill — matching cannot work without it.",
            code="vacancy_no_skills",
        )

    previous = vacancy.status
    vacancy.status = ModerationStatus.PENDING_REVIEW
    vacancy.save(update_fields=["status", "updated_at"])

    from apps.audit.services import log_status_change

    log_status_change(
        vacancy, from_status=previous, to_status=vacancy.status, actor=actor
    )
    return vacancy


@transaction.atomic
def moderate_vacancy(vacancy: Vacancy, *, approve: bool, actor, note: str = "") -> Vacancy:
    if vacancy.status != ModerationStatus.PENDING_REVIEW:
        raise Conflict("Only vacancies pending review can be moderated.")

    vacancy.status = (
        ModerationStatus.PUBLISHED if approve else ModerationStatus.REJECTED
    )
    vacancy.moderation_note = note
    vacancy.moderated_by = actor
    if approve:
        vacancy.published_at = timezone.now()
    vacancy.save(
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

    log_moderation(vacancy, decision=vacancy.status, actor=actor, note=note)
    notify(
        user=vacancy.employer.owner,
        type="MODERATION_RESULT",
        title_key="notifications.moderation.vacancy.title",
        body_key="notifications.moderation.vacancy.body",
        payload={"title": vacancy.title, "approved": approve, "note": note},
        ref_type="Vacancy",
        ref_id=vacancy.id,
        action_url=f"/employer/vacancies/{vacancy.id}",
    )

    if approve:
        _on_vacancy_published(vacancy)
    return vacancy


def _on_vacancy_published(vacancy: Vacancy) -> None:
    """Rank candidates and tell the strong matches (prompt §32)."""
    from apps.matching.models import MatchResult, MatchWeightProfile
    from apps.matching.services import recompute_matches_for_vacancy
    from apps.notifications.services import notify

    recompute_matches_for_vacancy(vacancy)

    threshold = MatchWeightProfile.active().min_score_to_notify
    strong = (
        MatchResult.objects.filter(vacancy=vacancy, overall_score__gte=threshold)
        .select_related("student")
        .order_by("-overall_score")[:50]
    )

    from apps.accounts.models import ConsentType
    from apps.accounts.services import has_consent

    for match in strong:
        # Only reach out to students who agreed to be found.
        if not has_consent(match.student, ConsentType.TALENT_SEARCH):
            continue
        notify(
            user=match.student,
            type="NEW_MATCH",
            title_key="notifications.match.new.title",
            body_key="notifications.match.new.body",
            payload={
                "title": vacancy.title,
                "company": vacancy.employer.display_name,
                "score": match.overall_score,
            },
            ref_type="Vacancy",
            ref_id=vacancy.id,
            action_url=f"/student/jobs/{vacancy.id}",
        )


def close_vacancy(vacancy: Vacancy, *, actor) -> Vacancy:
    previous = vacancy.status
    vacancy.status = ModerationStatus.ARCHIVED
    vacancy.closed_at = timezone.now()
    vacancy.save(update_fields=["status", "closed_at", "updated_at"])

    from apps.audit.services import log_status_change

    log_status_change(vacancy, from_status=previous, to_status=vacancy.status, actor=actor)
    return vacancy


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
@transaction.atomic
def apply_to_vacancy(*, student, vacancy: Vacancy, cv=None, cover_letter: str = "") -> Application:
    if not vacancy.is_open:
        raise NotAllowed("This vacancy is not accepting applications.", code="vacancy_closed")
    if Application.objects.filter(student=student, vacancy=vacancy).exists():
        raise Conflict("You have already applied to this vacancy.", code="already_applied")

    mandatory = vacancy.screening_tests.filter(is_mandatory=True).values_list(
        "test_id", flat=True
    )
    if mandatory:
        from apps.assessment.models import AttemptStatus, TestAttempt

        passed = TestAttempt.objects.filter(
            user=student,
            test_id__in=list(mandatory),
            passed=True,
            status=AttemptStatus.GRADED,
        ).values_list("test_id", flat=True)
        missing = set(mandatory) - set(passed)
        if missing:
            raise DomainError(
                "Complete the required screening test before applying.",
                code="screening_required",
                details={"test_ids": [str(t) for t in missing]},
            )

    from apps.matching.services import get_match

    match = get_match(student, vacancy)

    application = Application.objects.create(
        student=student,
        vacancy=vacancy,
        cv=cv,
        cover_letter=cover_letter,
        match_score_at_apply=match.overall_score if match else 0,
    )
    ApplicationEvent.objects.create(
        application=application,
        from_status="",
        to_status=ApplicationStatus.APPLIED,
        actor=student,
    )

    from apps.analytics.services import track
    from apps.learning.services import _complete_linked_tasks
    from apps.notifications.services import notify

    track(
        student,
        "application_submitted",
        {"vacancy_id": str(vacancy.id), "match_score": application.match_score_at_apply},
    )
    notify(
        user=vacancy.employer.owner,
        type="NEW_APPLICATION",
        title_key="notifications.application.new.title",
        body_key="notifications.application.new.body",
        payload={"vacancy": vacancy.title, "score": application.match_score_at_apply},
        ref_type="Application",
        ref_id=application.id,
        action_url=f"/employer/applications/{application.id}",
    )
    _complete_linked_tasks(student, ref_type="Vacancy", ref_id=vacancy.id)
    return application


@transaction.atomic
def change_application_status(
    application: Application, *, to_status: str, actor, note: str = ""
) -> Application:
    """Move an application along the funnel, enforcing legal transitions.

    Without this guard an application could jump APPLIED -> ACCEPTED and the
    funnel analytics in TZ §14 would quietly stop meaning anything.
    """
    current = application.status
    if to_status == current:
        return application

    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if to_status not in allowed:
        raise Conflict(
            f"Cannot move an application from {current} to {to_status}.",
            code="invalid_transition",
            details={"allowed": sorted(allowed)},
        )

    application.status = to_status
    application.status_changed_at = timezone.now()
    if note:
        application.employer_note = note
    application.save(
        update_fields=["status", "status_changed_at", "employer_note", "updated_at"]
    )

    ApplicationEvent.objects.create(
        application=application,
        from_status=current,
        to_status=to_status,
        actor=actor,
        note=note,
    )

    from apps.analytics.services import track
    from apps.audit.services import log_status_change
    from apps.notifications.services import notify

    log_status_change(application, from_status=current, to_status=to_status, actor=actor)
    track(
        application.student,
        "application_status_changed",
        {"application_id": str(application.id), "from": current, "to": to_status},
    )
    notify(
        user=application.student,
        type="APPLICATION_STATUS",
        title_key="notifications.application.status.title",
        body_key="notifications.application.status.body",
        payload={
            "vacancy": application.vacancy.title,
            "company": application.vacancy.employer.display_name,
            "status": to_status,
        },
        ref_type="Application",
        ref_id=application.id,
        action_url=f"/student/applications/{application.id}",
    )

    if to_status == ApplicationStatus.ACCEPTED:
        _on_hired(application, actor=actor)
    return application


def _on_hired(application: Application, *, actor) -> Placement:
    """Record the outcome the whole programme is measured by (TZ §15)."""
    from apps.analytics.services import track

    placement, created = Placement.objects.get_or_create(
        application=application,
        defaults={
            "student": application.student,
            "employer": application.vacancy.employer,
            "vacancy": application.vacancy,
            "position": application.vacancy.title,
            "start_date": timezone.localdate(),
            "confirmed_by": actor,
        },
    )
    if created:
        track(
            application.student,
            "hired",
            {
                "vacancy_id": str(application.vacancy_id),
                "employer_id": str(application.vacancy.employer_id),
            },
        )
    return placement


def withdraw_application(application: Application, *, actor) -> Application:
    return change_application_status(
        application, to_status=ApplicationStatus.WITHDRAWN, actor=actor
    )


def refresh_retention_flags() -> int:
    """Mark 30/90/180-day retention on active placements.

    Intended to run daily. Retention is the difference between "we placed
    people" and "we placed people who stayed" — the TZ asks for the second.
    """
    today = timezone.localdate()
    updated = 0
    for placement in Placement.objects.filter(status=PlacementStatus.ACTIVE):
        days = (today - placement.start_date).days
        end = placement.end_date
        fields = []
        for threshold, field in ((30, "retention_30"), (90, "retention_90"), (180, "retention_180")):
            if days >= threshold and getattr(placement, field) is None:
                setattr(placement, field, end is None or (end - placement.start_date).days >= threshold)
                fields.append(field)
        if fields:
            placement.save(update_fields=[*fields, "updated_at"])
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# Candidate search
# ---------------------------------------------------------------------------
def rank_candidates(vacancy: Vacancy, *, min_score: int = 0, limit: int = 100):
    """Ranked candidate list for the employer (prompt §18)."""
    from apps.matching.models import MatchResult
    from apps.matching.services import recompute_matches_for_vacancy

    queryset = MatchResult.objects.filter(vacancy=vacancy)
    if not queryset.exists():
        recompute_matches_for_vacancy(vacancy)
        queryset = MatchResult.objects.filter(vacancy=vacancy)

    return (
        queryset.filter(overall_score__gte=min_score)
        .select_related("student", "student__student_profile")
        .order_by("-overall_score")[:limit]
    )


# ---------------------------------------------------------------------------
# Interview invitations
#
# The employer's side of the funnel starts here when nobody applied. See the
# InterviewInvite docstring for why this is not simply "create an Application
# and schedule an interview".
# ---------------------------------------------------------------------------
@transaction.atomic
def invite_to_interview(
    *,
    vacancy: Vacancy,
    student,
    actor,
    message: str = "",
    proposed_at=None,
    duration_minutes: int = 45,
    mode: str = "ONLINE",
    location: str = "",
    meeting_link: str = "",
):
    """Ask a candidate to talk.

    If they already applied there is nothing to ask: the conversation is
    already open, so an Interview is scheduled outright and the application is
    walked to INTERVIEW. That path returns the application; the invitation
    path returns the invite. The caller can tell which by the object it gets.
    """
    from .models import InterviewInvite, InviteStatus

    if vacancy.status != ModerationStatus.PUBLISHED:
        raise Conflict(
            "Only a published vacancy can invite candidates.",
            code="vacancy_not_published",
        )

    existing = Application.objects.filter(student=student, vacancy=vacancy).first()
    if existing is not None:
        if not existing.is_active:
            raise Conflict(
                "This application is already closed.", code="application_closed"
            )
        # An open invitation can be timeless — "we would like to talk" is a
        # real message. A booking cannot: there is nobody left to agree the
        # time with, so no time means nothing would happen, and the caller
        # would be told an interview was scheduled when none was.
        if proposed_at is None:
            raise DomainError(
                "Pick a date and time: this candidate has already applied, "
                "so the interview is booked straight away.",
                code="interview_time_required",
            )

        interview = _schedule_interview(
            existing,
            actor=actor,
            scheduled_at=proposed_at,
            duration_minutes=duration_minutes,
            mode=mode,
            location=location,
            meeting_link=meeting_link,
        )
        return existing, interview

    open_invite = InterviewInvite.objects.filter(
        vacancy=vacancy, student=student, status=InviteStatus.PENDING
    ).first()
    if open_invite is not None:
        raise Conflict(
            "This candidate has already been invited and has not replied yet.",
            code="invite_already_open",
        )

    from apps.matching.models import MatchResult

    match = MatchResult.objects.filter(vacancy=vacancy, student=student).first()

    invite = InterviewInvite.objects.create(
        vacancy=vacancy,
        student=student,
        invited_by=actor,
        message=message,
        proposed_at=proposed_at,
        duration_minutes=duration_minutes,
        mode=mode,
        location=location,
        meeting_link=meeting_link,
        match_score_at_invite=match.overall_score if match else 0,
    )

    from apps.analytics.services import track
    from apps.notifications.services import notify

    track(student, "interview_invited", {"vacancy_id": str(vacancy.id)})
    notify(
        user=student,
        type="INTERVIEW_SCHEDULED",
        title_key="notifications.invite.received.title",
        body_key="notifications.invite.received.body",
        payload={
            "vacancy": vacancy.title,
            "company": vacancy.employer.display_name,
        },
        ref_type="InterviewInvite",
        ref_id=invite.id,
        action_url="/student/applications",
    )
    return invite, None


@transaction.atomic
def respond_to_invite(invite, *, accept: bool, note: str = ""):
    """The student's answer.

    Accepting is what creates the application - deliberately, because that is
    the moment the student chose to enter this company's process, and it is
    also the moment their name becomes visible to that employer.

    The application is created at SHORTLISTED rather than APPLIED. It did not
    come up through the funnel; the employer reached out and the candidate
    said yes. Recording it as a fresh application would misreport how the hire
    started, so the event trail says plainly where it came from.
    """
    from .models import InviteStatus

    if invite.status != InviteStatus.PENDING:
        raise Conflict("This invitation has already been answered.")

    invite.status = InviteStatus.ACCEPTED if accept else InviteStatus.DECLINED
    invite.responded_at = timezone.now()
    invite.response_note = note
    invite.save(update_fields=["status", "responded_at", "response_note", "updated_at"])

    if not accept:
        _notify_employer_of_response(invite, accepted=False)
        return invite, None, None

    from apps.matching.models import MatchResult

    match = MatchResult.objects.filter(
        vacancy=invite.vacancy, student=invite.student
    ).first()

    application, created = Application.objects.get_or_create(
        student=invite.student,
        vacancy=invite.vacancy,
        defaults={
            "status": ApplicationStatus.SHORTLISTED,
            "match_score_at_apply": match.overall_score if match else 0,
        },
    )
    if created:
        ApplicationEvent.objects.create(
            application=application,
            from_status="",
            to_status=ApplicationStatus.SHORTLISTED,
            actor=invite.student,
            note="Accepted an interview invitation from the employer.",
        )

    interview = None
    if invite.proposed_at is not None:
        interview = _schedule_interview(
            application,
            actor=invite.student,
            scheduled_at=invite.proposed_at,
            duration_minutes=invite.duration_minutes,
            mode=invite.mode,
            location=invite.location,
            meeting_link=invite.meeting_link,
            notify_student=False,
        )

    _notify_employer_of_response(invite, accepted=True)
    return invite, application, interview


def cancel_invite(invite, *, actor):
    """Withdraw an invitation the candidate has not answered yet."""
    from .models import InviteStatus

    if invite.status != InviteStatus.PENDING:
        raise Conflict("Only an unanswered invitation can be withdrawn.")
    invite.status = InviteStatus.CANCELLED
    invite.responded_at = timezone.now()
    invite.save(update_fields=["status", "responded_at", "updated_at"])
    return invite


def _schedule_interview(
    application,
    *,
    actor,
    scheduled_at,
    duration_minutes: int,
    mode: str,
    location: str = "",
    meeting_link: str = "",
    notify_student: bool = True,
):
    """Create the Interview and move the application onto it.

    The status walk is deliberate rather than a jump. ALLOWED_TRANSITIONS
    exists so the funnel keeps meaning something, and an interview arranged
    from a shortlist is still an application that passed through review - so
    it is recorded as having done so, one legal step at a time.
    """
    from .models import Interview

    path = {
        ApplicationStatus.APPLIED: [
            ApplicationStatus.UNDER_REVIEW,
            ApplicationStatus.SHORTLISTED,
            ApplicationStatus.INTERVIEW,
        ],
        ApplicationStatus.UNDER_REVIEW: [
            ApplicationStatus.SHORTLISTED,
            ApplicationStatus.INTERVIEW,
        ],
        ApplicationStatus.SHORTLISTED: [ApplicationStatus.INTERVIEW],
    }.get(application.status, [])

    for step in path:
        change_application_status(
            application,
            to_status=step,
            actor=actor,
            note="Interview arranged." if step == ApplicationStatus.INTERVIEW else "",
        )

    interview = Interview.objects.create(
        application=application,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        mode=mode,
        location=location,
        meeting_link=meeting_link,
    )

    if notify_student:
        from apps.notifications.services import notify

        notify(
            user=application.student,
            type="INTERVIEW_SCHEDULED",
            title_key="notifications.interview.scheduled.title",
            body_key="notifications.interview.scheduled.body",
            payload={
                "vacancy": application.vacancy.title,
                "scheduled_at": scheduled_at.isoformat(),
            },
            ref_type="Interview",
            ref_id=interview.id,
            action_url=f"/student/applications/{application.id}",
        )
    return interview


def _notify_employer_of_response(invite, *, accepted: bool) -> None:
    from apps.notifications.services import notify

    recipient = invite.invited_by or getattr(invite.vacancy.employer, "owner", None)
    if recipient is None:
        return
    notify(
        user=recipient,
        type="NEW_APPLICATION" if accepted else "APPLICATION_STATUS",
        title_key=(
            "notifications.invite.accepted.title"
            if accepted
            else "notifications.invite.declined.title"
        ),
        body_key=(
            "notifications.invite.accepted.body"
            if accepted
            else "notifications.invite.declined.body"
        ),
        payload={"vacancy": invite.vacancy.title},
        ref_type="InterviewInvite",
        ref_id=invite.id,
        action_url=f"/employer/vacancies/{invite.vacancy_id}/candidates",
    )
