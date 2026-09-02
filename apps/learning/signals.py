"""Course-level invalidation.

Lesson and enrolment progress are handled explicitly in `services.py`, where
the whole ripple is visible in one place. Only changes that can arrive from the
admin or a data import land here.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import CourseSkill


@receiver(post_save, sender=CourseSkill, dispatch_uid="course_skill_saved")
@receiver(post_delete, sender=CourseSkill, dispatch_uid="course_skill_deleted")
def on_course_skill_changed(sender, instance: CourseSkill, **kwargs) -> None:
    """Changing what a course teaches changes what its graduates are credited
    with, so their evidence needs rebuilding on the next recompute."""
    from apps.learning.models import Enrollment, EnrollmentStatus
    from apps.matching.services import mark_matches_stale

    graduate_ids = Enrollment.objects.filter(
        course_id=instance.course_id, status=EnrollmentStatus.COMPLETED
    ).values_list("user_id", flat=True)

    for user_id in graduate_ids:
        mark_matches_stale(student=user_id)
