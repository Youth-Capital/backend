"""Mentor assessments become skill evidence; ratings roll up to the profile."""

from django.db.models import Avg, Count
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.common.enums import EvidenceSource, VerificationStatus

from .models import MentorFeedback, MentorSession, MentorSkillAssessment, SessionStatus


@receiver(post_save, sender=MentorSkillAssessment, dispatch_uid="mentor_skill_assessed")
def on_skill_assessed(sender, instance: MentorSkillAssessment, **kwargs) -> None:
    """A mentor's judgement is stronger than self-report, weaker than a test."""
    from apps.profiles.services import record_skill_evidence

    session = instance.feedback.session
    # Only the mentor's own assessment counts — a student rating themselves in
    # the feedback form must not become evidence.
    if instance.feedback.author_id != session.mentor.user_id:
        return

    # And only a *verified* mentor's. Mentor sign-up is self-service and starts
    # PENDING, so without this two accounts controlled by one person — a student
    # and an unverified "mentor" — could mint MENTOR-weight evidence (0.85),
    # just below a graded test. Booking is blocked for unverified mentors too;
    # this is the check that actually guards the evidence model.
    if session.mentor.verification_status != VerificationStatus.VERIFIED:
        return

    record_skill_evidence(
        user=session.student,
        skill=instance.skill,
        source=EvidenceSource.MENTOR,
        score=instance.score,
        ref_type="MentorSession",
        ref_id=session.id,
        issued_by=instance.feedback.author,
        note=f"Mentor assessment: {session.topic}"[:255],
    )


@receiver(post_save, sender=MentorFeedback, dispatch_uid="mentor_feedback_saved")
def on_feedback_saved(sender, instance: MentorFeedback, **kwargs) -> None:
    """Recompute the mentor's public rating from student feedback only."""
    session = instance.session
    mentor = session.mentor

    # A mentor rating themselves would inflate their own score.
    stats = MentorFeedback.objects.filter(session__mentor=mentor).exclude(
        author_id=mentor.user_id
    ).aggregate(avg=Avg("rating"), count=Count("id"))

    mentor.rating_avg = round(stats["avg"] or 0, 2)
    mentor.rating_count = stats["count"] or 0
    mentor.save(update_fields=["rating_avg", "rating_count", "updated_at"])


@receiver(post_save, sender=MentorSession, dispatch_uid="mentor_session_saved")
def on_session_saved(sender, instance: MentorSession, **kwargs) -> None:
    if instance.status != SessionStatus.COMPLETED:
        return
    mentor = instance.mentor
    mentor.sessions_count = MentorSession.objects.filter(
        mentor=mentor, status=SessionStatus.COMPLETED
    ).count()
    mentor.save(update_fields=["sessions_count", "updated_at"])
