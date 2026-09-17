"""Automatic feedback triggers.

The moments worth asking at are the ones where something just happened: a
course finished, a job started. Asking then gets an
answer about something concrete; asking on a Tuesday gets "fine, thanks".

Every handler goes through :func:`request_review`, which holds the cooldowns —
so a student who finishes three courses in a fortnight is asked once, and these
handlers stay free of "have we asked recently" logic they would each get subtly
wrong.

The signals are registered here rather than in `learning` and `jobs`, so
the whole feature can be read (or removed) in one place.
"""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.jobs.models import Placement
from apps.learning.models import Enrollment, EnrollmentStatus

from .models import ReviewTrigger
from .services import request_review


@receiver(post_save, sender=Enrollment, dispatch_uid="feedback_course_completed")
def on_course_completed(sender, instance: Enrollment, **kwargs) -> None:
    if instance.status != EnrollmentStatus.COMPLETED:
        return
    request_review(
        instance.user,
        trigger=ReviewTrigger.COURSE_COMPLETED,
        ref_type="Course",
        ref_id=instance.course_id,
    )


@receiver(post_save, sender=Placement, dispatch_uid="feedback_placement_created")
def on_placement_created(sender, instance: Placement, created: bool, **kwargs) -> None:
    """The outcome the whole platform exists for — and both sides were there.

    Asking the employer as well as the student is deliberate: a placement that
    the student is happy with and the employer is not is the single most
    important thing this feedback loop can surface, and only one of them can
    tell you.
    """
    if not created:
        return

    request_review(
        instance.student,
        trigger=ReviewTrigger.PLACEMENT,
        ref_type="Placement",
        ref_id=instance.id,
    )

    owner = getattr(instance.employer, "owner", None)
    if owner is not None:
        request_review(
            owner,
            trigger=ReviewTrigger.HIRED_CANDIDATE,
            ref_type="Placement",
            ref_id=instance.id,
        )

