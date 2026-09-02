"""Keep derived data in sync when a profile's skills change.

Signals are used only where a change can arrive through more than one path
(admin, seeder, API, service). Anything with a single entry point is called
explicitly from the service instead — implicit side effects are harder to
follow than a function call.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import UserSkill


@receiver(post_save, sender=UserSkill, dispatch_uid="user_skill_saved")
def on_user_skill_saved(sender, instance: UserSkill, **kwargs) -> None:
    _invalidate_matches(instance.user_id)


@receiver(post_delete, sender=UserSkill, dispatch_uid="user_skill_deleted")
def on_user_skill_deleted(sender, instance: UserSkill, **kwargs) -> None:
    _invalidate_matches(instance.user_id)


def _invalidate_matches(user_id) -> None:
    """Flag matches for recomputation; never recompute inline.

    A bulk skill import would otherwise trigger one full matching pass per row.
    Passing the id rather than the object avoids a needless user fetch.
    """
    from apps.matching.services import mark_matches_stale

    mark_matches_stale(student=user_id)
