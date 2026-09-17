"""Taxonomy lookups that more than one app needs.

Small on purpose: the taxonomy is reference data, so the interesting questions
about it are "which rows belong to this branch?" rather than anything with a
lifecycle.
"""

from __future__ import annotations

from .models import Skill, SkillCategory


def soft_skill_category_ids() -> set:
    """Every category in a branch flagged ``is_soft_skill``.

    Walked in Python rather than with a recursive CTE: the tree is dozens of
    rows, admin-managed, and read constantly — two flat queries beat a
    database-specific query that SQLite and PostgreSQL would answer
    differently.
    """
    rows = list(SkillCategory.objects.values("id", "parent_id", "is_soft_skill"))
    by_id = {row["id"]: row for row in rows}
    roots = {row["id"] for row in rows if row["is_soft_skill"]}

    soft: set = set()
    for row in rows:
        node, guard = row, 0
        while node is not None and guard < 10:
            if node["id"] in roots:
                soft.add(row["id"])
                break
            node = by_id.get(node["parent_id"])
            guard += 1
    return soft


def soft_skill_ids() -> set:
    """Ids of every skill that sits under a soft-skill category."""
    categories = soft_skill_category_ids()
    if not categories:
        return set()
    return set(
        Skill.objects.filter(category_id__in=categories).values_list("id", flat=True)
    )


def split_hard_soft(user_skills) -> tuple[list, list]:
    """Partition an iterable of UserSkill rows into (hard, soft)."""
    soft_ids = soft_skill_ids()
    hard, soft = [], []
    for link in user_skills:
        (soft if link.skill_id in soft_ids else hard).append(link)
    return hard, soft
