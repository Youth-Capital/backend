"""Assessment invalidation.

Grading and its consequences live in `services.py`. This module only handles
edits that bypass the grading path — an admin retagging a test's skills, for
example.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import TestSkill


@receiver(post_save, sender=TestSkill, dispatch_uid="test_skill_saved")
@receiver(post_delete, sender=TestSkill, dispatch_uid="test_skill_deleted")
def on_test_skill_changed(sender, instance: TestSkill, **kwargs) -> None:
    from apps.matching.services import mark_matches_stale

    from .models import AttemptStatus, TestAttempt

    taker_ids = (
        TestAttempt.objects.filter(
            test_id=instance.test_id, status=AttemptStatus.GRADED
        )
        .values_list("user_id", flat=True)
        .distinct()
    )
    for user_id in taker_ids:
        mark_matches_stale(student=user_id)
