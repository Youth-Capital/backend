"""The assistant's prompt is composed per person, and it has to stay safe.

Two things are being protected here, and the second is the one that would go
wrong quietly. The first is that the prompt actually adapts — a new learner and
a job-ready one should not get the same instructions. The second is that
adapting never costs a safety rule: the register changes, what may be said does
not, and a refactor that composes the blocks in a different order must not be
able to drop the block that says an employer never sees a candidate's name.
"""

import pytest

from apps.ai import persona
from apps.common.enums import EvidenceSource, Role

pytestmark = pytest.mark.django_db

PREFS_URL = "/api/v1/ai/assistant/preferences/"


def make_user(role, email):
    from apps.accounts.models import User

    return User.objects.create_user(email=email, password="TestPass12345", role=role)


# -- the safety floor holds whoever is asking ------------------------------
@pytest.mark.parametrize("role", [Role.STUDENT, Role.EMPLOYER, Role.ADMIN])
@pytest.mark.parametrize(
    "prefs",
    [
        {},
        {"detail": "BRIEF", "tone": "DIRECT", "explain_terms": False},
        {"detail": "DETAILED", "tone": "WARM", "explain_terms": True},
    ],
)
def test_every_composed_prompt_carries_the_safety_rules(role, prefs, db):
    """Whatever the register, the rules are in there verbatim."""
    user = make_user(role, f"{role.lower()}-{len(prefs)}@example.com")

    prompt = persona.build_system_prompt(user, language="Russian", preferences=prefs)

    assert "Never invent or recompute a number" in prompt
    assert "Never reveal a candidate's name" in prompt
    assert "not a doctor, lawyer or financial adviser" in prompt


def test_the_employer_block_never_reaches_a_student(student):
    """Role blocks are exclusive: they say what this person may be told."""
    prompt = persona.build_system_prompt(student, language="Russian")

    assert "learner about their own progress" in prompt
    assert "candidates matched to them" not in prompt


def test_the_student_block_never_reaches_an_employer(employer):
    prompt = persona.build_system_prompt(employer, language="Russian")

    assert "candidates matched to them" in prompt
    assert "learner about their own progress" not in prompt


def test_an_employer_gets_no_stage_block(employer):
    """A stage describes a learner's evidence. An employer has none, and
    telling the model an employer is a beginner would be a claim about the
    wrong thing entirely."""
    prompt = persona.build_system_prompt(employer, language="Russian")

    for block in persona.STAGE_BLOCKS.values():
        assert block not in prompt


# -- it actually adapts ----------------------------------------------------
def test_the_stage_blocks_say_genuinely_different_things():
    assert persona.STAGE_BLOCKS["new"] != persona.STAGE_BLOCKS["ready"]
    assert "one step, not a plan" in persona.STAGE_BLOCKS["new"]
    assert "do not need encouragement" in persona.STAGE_BLOCKS["ready"]


def test_defaults_soften_for_a_beginner_and_sharpen_for_the_experienced():
    assert persona.default_preferences("new") == {
        "detail": "DETAILED",
        "tone": "WARM",
        "explain_terms": True,
    }
    assert persona.default_preferences("ready")["tone"] == "DIRECT"
    assert persona.default_preferences("ready")["explain_terms"] is False


def test_a_chosen_preference_beats_the_derived_default(student):
    """The setting is a correction, so it has to win."""
    prompt = persona.build_system_prompt(
        student, language="Russian", preferences={"detail": "BRIEF"}
    )

    assert persona.DETAIL_BLOCKS["BRIEF"] in prompt
    assert persona.DETAIL_BLOCKS["DETAILED"] not in prompt


def test_the_language_is_named_in_the_prompt(student):
    assert "Write in Uzbek (latin script)" in persona.build_system_prompt(
        student, language="Uzbek (latin script)"
    )


# -- stage is read from evidence, not from self-assessment -----------------
def test_a_brand_new_account_is_new(student):
    assert persona.stage_for(student) == "new"


def test_self_declared_skills_do_not_advance_the_stage(student, taxonomy):
    """Otherwise an optimistic self-assessment reads as a job-ready profile."""
    from apps.profiles.models import UserSkill

    for skill in (taxonomy["python"], taxonomy["sql"], taxonomy["power_bi"]):
        UserSkill.objects.create(
            user=student, skill=skill, proficiency=90, best_source=EvidenceSource.SELF
        )

    assert persona.stage_for(student) == "new"


def test_a_confirmed_skill_does_advance_the_stage(student, taxonomy):
    from apps.profiles.models import UserSkill

    UserSkill.objects.create(
        user=student,
        skill=taxonomy["sql"],
        proficiency=70,
        best_source=EvidenceSource.TEST,
    )

    assert persona.stage_for(student) == "building"


# -- the preferences endpoint ----------------------------------------------
def test_preferences_start_unset_and_report_the_derived_defaults(auth, student):
    response = auth(student).get(PREFS_URL)

    assert response.status_code == 200, response.data
    assert response.data["chosen"] == {
        "detail": None,
        "tone": None,
        "explain_terms": None,
    }
    # Nothing chosen, so effective is entirely derived — and the two halves
    # being reported separately is the point of the endpoint.
    assert response.data["effective"]["tone"] == "WARM"
    assert response.data["stage"] == "new"


def test_a_preference_can_be_set_and_cleared_again(auth, student):
    client = auth(student)

    set_response = client.patch(PREFS_URL, {"detail": "BRIEF"}, format="json")
    assert set_response.status_code == 200, set_response.data
    assert set_response.data["chosen"]["detail"] == "BRIEF"
    assert set_response.data["effective"]["detail"] == "BRIEF"

    # null goes back to the adaptive default rather than to a fixed value.
    cleared = client.patch(PREFS_URL, {"detail": None}, format="json")
    assert cleared.data["chosen"]["detail"] is None
    assert cleared.data["effective"]["detail"] == "DETAILED"


def test_an_unknown_preference_value_is_refused(auth, student):
    response = auth(student).patch(PREFS_URL, {"tone": "SARCASTIC"}, format="json")
    assert response.status_code == 400, response.data


def test_preferences_are_private_to_their_owner(auth, student, employer):
    auth(student).patch(PREFS_URL, {"tone": "DIRECT"}, format="json")

    other = auth(employer).get(PREFS_URL)
    assert other.data["chosen"]["tone"] is None
