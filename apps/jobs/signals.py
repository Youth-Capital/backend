"""Invalidate matches when a vacancy's requirements change."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Vacancy, VacancySkill


@receiver(post_save, sender=VacancySkill, dispatch_uid="vacancy_skill_saved")
@receiver(post_delete, sender=VacancySkill, dispatch_uid="vacancy_skill_deleted")
def on_vacancy_skill_changed(sender, instance: VacancySkill, **kwargs) -> None:
    from apps.matching.services import mark_matches_stale

    mark_matches_stale(vacancy=instance.vacancy_id)


@receiver(post_save, sender=Vacancy, dispatch_uid="vacancy_saved")
def on_vacancy_saved(sender, instance: Vacancy, created: bool, **kwargs) -> None:
    """Requirement fields feed the score, so edits must invalidate it.

    Skipped on creation: a brand new vacancy has no matches to invalidate.
    """
    if created:
        return
    from apps.matching.services import mark_matches_stale

    mark_matches_stale(vacancy=instance.pk)
