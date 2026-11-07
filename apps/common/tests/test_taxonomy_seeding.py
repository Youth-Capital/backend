"""The reference data the platform cannot function without.

Regions, skills, the capital axes and the professions every other screen is
built around are not optional content — the career page, the intake interview
and the development plan all assume rows exist. A database that has them is
indistinguishable from a broken product from the outside: the select offers
only "Not specified", the interview asks a question with no answers, and the
plan demands a profession that cannot be chosen. That is what a freshly
provisioned production database served, because nothing ensured the seeding
had been run on it.

So two things are pinned here. That the command may be run on every start
without damaging a database that already has the data, and that the container
actually runs it.
"""

from pathlib import Path

import pytest
from django.core.management import call_command

from apps.taxonomy.models import (
    CapitalDimension,
    Profession,
    ProfessionSkill,
    Region,
    Skill,
    SkillCategory,
)

pytestmark = pytest.mark.django_db

ENTRYPOINT = Path(__file__).resolve().parents[3] / "entrypoint.sh"


def counts() -> dict[str, int]:
    return {
        "regions": Region.objects.count(),
        "categories": SkillCategory.objects.count(),
        "skills": Skill.objects.count(),
        "dimensions": CapitalDimension.objects.count(),
        "professions": Profession.objects.count(),
        "profession_skills": ProfessionSkill.objects.count(),
    }


# ---------------------------------------------------------------------------
# The command fills an empty database
# ---------------------------------------------------------------------------
def test_seeding_an_empty_database_produces_the_reference_data():
    assert Profession.objects.count() == 0

    call_command("seed_taxonomy", verbosity=0)

    after = counts()
    assert after["professions"] > 0, "the career page has nothing to offer without these"
    assert after["skills"] > 0
    assert after["regions"] > 0
    assert after["dimensions"] > 0


def test_every_profession_names_the_skills_it_wants():
    """A profession with no required skills cannot produce a skill gap, and the
    gap is what the plan and the course matching are built from."""
    call_command("seed_taxonomy", verbosity=0)

    for profession in Profession.objects.all():
        assert profession.skill_links.exists(), profession.slug


def test_the_professions_are_named_in_all_three_languages():
    call_command("seed_taxonomy", verbosity=0)

    for profession in Profession.objects.all():
        assert profession.name_uz, profession.slug
        assert profession.name_ru, profession.slug
        assert profession.name_en, profession.slug


# ---------------------------------------------------------------------------
# Running it again changes nothing
# ---------------------------------------------------------------------------
def test_seeding_twice_leaves_the_database_as_it_was():
    """This is what makes it safe to run on every container start."""
    call_command("seed_taxonomy", verbosity=0)
    first = counts()

    call_command("seed_taxonomy", verbosity=0)

    assert counts() == first


def test_a_second_run_keeps_the_same_rows_rather_than_replacing_them():
    """Replacing them would break every profile pointing at a profession."""
    call_command("seed_taxonomy", verbosity=0)
    before = set(Profession.objects.values_list("id", flat=True))

    call_command("seed_taxonomy", verbosity=0)

    assert set(Profession.objects.values_list("id", flat=True)) == before


# ---------------------------------------------------------------------------
# The container runs it
# ---------------------------------------------------------------------------
def test_the_entrypoint_seeds_the_taxonomy():
    script = ENTRYPOINT.read_text(encoding="utf-8")

    assert "seed_taxonomy" in script, (
        "a freshly provisioned database serves an empty catalogue without this"
    )


def test_the_seeding_runs_after_the_migrations():
    """There is nothing to seed into until the tables exist."""
    script = ENTRYPOINT.read_text(encoding="utf-8")

    assert script.index("manage.py migrate") < script.index("manage.py seed_taxonomy")


def test_the_seeding_runs_before_the_server_accepts_traffic():
    script = ENTRYPOINT.read_text(encoding="utf-8")

    assert script.index("manage.py seed_taxonomy") < script.index("exec gunicorn")


def test_the_entrypoint_never_resets_the_taxonomy():
    """--reset deletes every profession first, which on a running platform
    would take the target profession off every profile that points at one."""
    script = ENTRYPOINT.read_text(encoding="utf-8")

    seeding = next(
        line for line in script.splitlines() if "manage.py seed_taxonomy" in line
    )
    assert "--reset" not in seeding
