"""Turn experience entries into skill evidence."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.common.enums import EvidenceSource

from .models import Experience, ExperienceSkill


@receiver(post_save, sender=ExperienceSkill, dispatch_uid="experience_skill_saved")
def on_experience_skill_saved(sender, instance: ExperienceSkill, created: bool, **kwargs):
    if not created:
        return
    _record(instance.experience, instance.skill)


@receiver(post_delete, sender=ExperienceSkill, dispatch_uid="experience_skill_deleted")
def on_experience_skill_deleted(sender, instance: ExperienceSkill, **kwargs):
    from apps.profiles.models import SkillEvidence, UserSkill
    from apps.profiles.services import recalculate_user_skill

    user_skill = UserSkill.objects.filter(
        user_id=instance.experience.user_id, skill_id=instance.skill_id
    ).first()
    if user_skill is None:
        return
    SkillEvidence.objects.filter(
        user_skill=user_skill,
        source=EvidenceSource.EXPERIENCE,
        ref_type="Experience",
        ref_id=instance.experience_id,
    ).delete()
    recalculate_user_skill(user_skill)


def _record(experience: Experience, skill) -> None:
    """Score experience by duration, not by the fact it was typed in.

    Six years of unverified self-entered work should not read the same as six
    months, and none of it should read like a passed exam — the EXPERIENCE
    source weight (0.70) keeps it below tested evidence.
    """
    from apps.profiles.services import record_skill_evidence

    months = experience.duration_months
    score = min(90, 35 + months * 2)

    record_skill_evidence(
        user=experience.user,
        skill=skill,
        source=EvidenceSource.EXPERIENCE,
        score=score,
        ref_type="Experience",
        ref_id=experience.id,
        note=f"{experience.title} · {months} months"[:255],
    )
