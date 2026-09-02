"""Give every new account its role's free plan.

Doing this on creation rather than lazily means the billing page, usage
counters and upgrade flow all read one shape from the very first request,
instead of each having to special-case "no subscription yet".
"""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.common.enums import Role

from .services import ensure_subscription


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="billing_default_plan")
def attach_default_plan(sender, instance, created, **kwargs):
    if not created or instance.role not in {Role.STUDENT, Role.EMPLOYER}:
        return
    try:
        ensure_subscription(instance)
    except RuntimeError:
        # Plans are not seeded yet (fresh database, or a migration run before
        # seed_plans). The lazy path in the API will create it on first read.
        pass
