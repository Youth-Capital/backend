"""Retire mentorship: the data, then the tables it lived in.

Runs before 0005, which changes the `source` choices and drops the
MentorProfile model. The order is not cosmetic:

  * A `choices` change is metadata only in PostgreSQL. Nothing checks the
    column, so rows holding 'MENTOR' would survive the alteration and become
    strings the application can no longer interpret — a skill whose evidence
    has a source that is not in the enum. They have to go first.

  * The mentorship tables carry a foreign key to `profiles_mentor`. Dropping
    that table while `mentorship_session` still points at it fails on the
    constraint, so those tables have to go first too. The `mentorship` app is
    no longer installed, so its own migrations can never run again — nothing
    else is going to drop them.

WHAT THIS DESTROYS, DELIBERATELY

Four skills on this platform had a mentor's assessment as their strongest
evidence. Removing that evidence lowers them to whatever else they have —
usually a self-declaration at 0.35 confidence instead of 0.85 — which changes
their match scores. That is the honest outcome of removing the role: the
platform can no longer claim a mentor confirmed something, because it no
longer has mentors. Every affected skill is recalculated here from the
evidence that remains, so nothing is left holding a number it can no longer
justify.
"""

from django.db import migrations


def _recalculate(user_skill, EvidenceSource, EVIDENCE_WEIGHTS):
    """Re-derive one skill from the evidence still attached to it.

    A trimmed copy of profiles.services.recalculate_user_skill rather than an
    import of it: a migration must keep working against the models as they
    were at this point in history, and the service will go on changing.
    """
    evidence = list(user_skill.evidence.all())
    if not evidence:
        user_skill.proficiency = 0
        user_skill.confidence = 0
        user_skill.best_source = EvidenceSource.SELF
        user_skill.status = "DECLARED"
        user_skill.save(
            update_fields=[
                "proficiency", "confidence", "best_source", "status", "updated_at"
            ]
        )
        return

    best = max(evidence, key=lambda e: EVIDENCE_WEIGHTS.get(e.source, 0.35))
    weight = EVIDENCE_WEIGHTS.get(best.source, 0.35)

    user_skill.proficiency = max(e.level for e in evidence)
    user_skill.confidence = weight
    user_skill.best_source = best.source
    user_skill.status = (
        "VERIFIED"
        if best.source in {EvidenceSource.TEST, EvidenceSource.EMPLOYER}
        else "DECLARED"
    )
    user_skill.save(
        update_fields=[
            "proficiency", "confidence", "best_source", "status", "updated_at"
        ]
    )


def retire(apps, schema_editor):
    from apps.common.enums import EVIDENCE_WEIGHTS, EvidenceSource

    SkillEvidence = apps.get_model("profiles", "SkillEvidence")
    UserSkill = apps.get_model("profiles", "UserSkill")
    User = apps.get_model("accounts", "User")

    # 1. The evidence, and the skills that leaned on it.
    mentor_evidence = SkillEvidence.objects.filter(source="MENTOR")
    affected = set(mentor_evidence.values_list("user_skill_id", flat=True))
    mentor_evidence.delete()

    for user_skill in UserSkill.objects.filter(id__in=affected):
        _recalculate(user_skill, EvidenceSource, EVIDENCE_WEIGHTS)

    # A skill whose *best* source was a mentor but whose evidence rows were
    # already gone — a dangling summary with nothing behind it.
    for user_skill in UserSkill.objects.filter(best_source="MENTOR"):
        _recalculate(user_skill, EvidenceSource, EVIDENCE_WEIGHTS)

    # 2. The mentorship tables, and ONLY those.
    #
    #    Raw SQL because the app is uninstalled: there is no model left to hand
    #    to a DeleteModel operation, and its own migrations can never run
    #    again, so nothing else will ever drop them.
    #
    #    `profiles_mentor` and its two m2m tables are deliberately absent from
    #    this list. They belong to the DeleteModel in 0005, which drops them
    #    itself — dropping them here as well left 0005 deleting tables that
    #    were already gone, which SQLite treats as an error rather than a
    #    no-op.
    #
    #    CASCADE is PostgreSQL-only; SQLite rejects it as a syntax error, so
    #    the clause is emitted per vendor rather than assumed.
    cascade = " CASCADE" if schema_editor.connection.vendor == "postgresql" else ""
    with schema_editor.connection.cursor() as cursor:
        for table in (
            "mentorship_session_skills",
            "mentorship_review",
            "mentorship_session",
        ):
            cursor.execute("DROP TABLE IF EXISTS %s%s" % (table, cascade))

    # 3. The accounts. Last, so the cascades above have already taken anything
    #    that pointed at them.
    User.objects.filter(role="MENTOR").delete()


def unretire(apps, schema_editor):
    raise RuntimeError(
        "Mentorship cannot be restored: this migration deleted the sessions, "
        "the mentor accounts and the assessments they issued. Restore from a "
        "backup taken before it ran."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0003_alter_skillevidence_source_and_more"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(retire, unretire),
    ]
