"""Rewrite generated plan text as translation keys.

The plan generator used to write finished English sentences. Every plan made
before this migration therefore reads in English no matter which language the
student has selected — and those plans are live, so regenerating them would
throw away completed tasks.

The text was machine-written from a fixed set of templates, so it can be
matched exactly and turned back into the key it should have been. Anything
that does not match one of the templates is left alone: it was typed by a
person, and a person's words are not ours to rewrite.
"""

from __future__ import annotations

import re

from django.db import migrations

TITLE = re.compile(r"^(\d+)-day plan: (.+)$")
SUMMARY = re.compile(r"^(\d+) tasks across (\d+) milestones, targeting (.+)\.$")
CLOSE_GAP = re.compile(r"^Close the gap in (.+)\.$")
REACH_LEVEL = re.compile(r"^Reach level (\d+) in (.+)\.$")
PORTFOLIO = "Apply what you learned and add it to your portfolio."


def to_keys(apps, schema_editor):
    DevelopmentPlan = apps.get_model("idp", "DevelopmentPlan")
    Task = apps.get_model("idp", "Task")

    for plan in DevelopmentPlan.objects.all().iterator():
        fields = []
        title = TITLE.match(plan.title or "")
        if title:
            plan.title = (
                f"plan.generated.title::days={title.group(1)}"
                f"::profession={title.group(2)}"
            )
            fields.append("title")
        summary = SUMMARY.match(plan.summary or "")
        if summary:
            plan.summary = (
                f"plan.generated.summary::tasks={summary.group(1)}"
                f"::milestones={summary.group(2)}::profession={summary.group(3)}"
            )
            fields.append("summary")
        if fields:
            plan.save(update_fields=fields)

    for task in Task.objects.exclude(description="").iterator():
        description = task.description or ""
        if description == PORTFOLIO:
            task.description = "plan.desc.portfolio"
        elif gap := CLOSE_GAP.match(description):
            task.description = f"plan.desc.close_gap::skill={gap.group(1)}"
        elif level := REACH_LEVEL.match(description):
            task.description = (
                f"plan.desc.reach_level::skill={level.group(2)}"
                f"::level={level.group(1)}"
            )
        else:
            continue
        task.save(update_fields=["description"])


def to_english(apps, schema_editor):
    """Reverse by rendering the keys back into the sentences they replaced."""
    DevelopmentPlan = apps.get_model("idp", "DevelopmentPlan")
    Task = apps.get_model("idp", "Task")

    def args(value):
        return dict(
            part.split("=", 1) for part in value.split("::")[1:] if "=" in part
        )

    for plan in DevelopmentPlan.objects.all().iterator():
        fields = []
        if (plan.title or "").startswith("plan.generated.title::"):
            a = args(plan.title)
            plan.title = f"{a.get('days', '90')}-day plan: {a.get('profession', '')}"
            fields.append("title")
        if (plan.summary or "").startswith("plan.generated.summary::"):
            a = args(plan.summary)
            plan.summary = (
                f"{a.get('tasks', '0')} tasks across {a.get('milestones', '0')} "
                f"milestones, targeting {a.get('profession', '')}."
            )
            fields.append("summary")
        if fields:
            plan.save(update_fields=fields)

    for task in Task.objects.filter(description__startswith="plan.desc.").iterator():
        a = args(task.description)
        if task.description.startswith("plan.desc.close_gap"):
            task.description = f"Close the gap in {a.get('skill', '')}."
        elif task.description.startswith("plan.desc.reach_level"):
            task.description = (
                f"Reach level {a.get('level', '')} in {a.get('skill', '')}."
            )
        elif task.description == "plan.desc.portfolio":
            task.description = PORTFOLIO
        else:
            continue
        task.save(update_fields=["description"])


class Migration(migrations.Migration):
    dependencies = [("idp", "0002_learningstreak")]
    operations = [migrations.RunPython(to_keys, to_english)]
