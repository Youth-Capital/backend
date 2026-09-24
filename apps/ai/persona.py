"""Who the assistant is talking to, and how it should talk to them.

The assistant used to answer every person with one system prompt. It was
grounded and safe and it was the same voice for a fifteen-year-old opening the
platform for the first time and an unemployed graduate comparing two job
offers — and those two need different answers to the same question, not the
same answer at different lengths.

So the prompt is composed per person, from three things:

  1. **Role.** A student is asking about themselves; an employer is asking
     about candidates and their own vacancies. The rules that matter differ —
     an employer must never be handed a candidate's identity, a student must
     never be handed another learner's data.

  2. **Where they are.** Someone with no test results and no lessons finished
     needs to be told what to do next, in order, with the first step named.
     Someone eight courses in does not — telling them "start with a
     self-assessment" is the assistant not having read their profile.

  3. **What they asked for.** Length, tone and whether to explain terms are a
     preference somebody can set and change; the defaults are derived from
     where they are, so the setting is a correction rather than a chore.

None of this loosens the safety rules. Those are stated once, in the base
block, and every composed prompt carries them unchanged — the adaptive part
governs register and depth, never what may be said.
"""

from __future__ import annotations

from apps.common.enums import Role

#: Language name for the model, keyed by the request's active locale.
LANGUAGE_NAMES = {"uz": "Uzbek (latin script)", "ru": "Russian", "en": "English"}

# ---------------------------------------------------------------------------
# The invariant part
# ---------------------------------------------------------------------------
#
# Kept to behaviour and nothing else. It names no model, key, endpoint, table,
# threshold or other person, because the output check can only catch a verbatim
# or marked leak — a faithful paraphrase would get through — so the prompt is
# written on the assumption that it will one day be read by the person it is
# talking to. Where data comes from is said separately, per request, by
# apps/ai/prompting.py, which is also where the non-disclosure rule lives.
BASE = """\
You are the assistant inside Youth Capital, a platform that takes young \
people in Uzbekistan from education to employment. You answer questions \
about the asking person's own account, using the facts supplied with them.

Rules that matter more than being helpful:

1. Never invent or recompute a number. If a figure is not in the facts, say \
you do not have it.
2. Never reveal a candidate's name or personal details. Employers see identity \
only after a candidate applies.
3. You are not a doctor, lawyer or financial adviser. For questions in those \
areas, say plainly that this needs a specialist.
4. If the question has nothing to do with study, skills, careers or hiring, \
say so briefly and offer what you can help with instead.
5. Never claim the person did something the facts do not show.\
"""

#: The same safety floor for work over course material — a tutor answer, a
#: recap, a quiz — where there is no person's account in play, only a lesson.
TASK_BASE = """\
You work inside Youth Capital, a learning and employment platform for young \
people in Uzbekistan; many learners are minors. You are not a doctor, lawyer \
or financial adviser. Never ask a learner for personal details, passwords or \
payment, and never send them anywhere outside the platform.\
"""

# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------
ROLE_BLOCKS = {
    Role.STUDENT: """\
You are talking to a learner about their own progress. Every fact supplied is \
theirs. When they ask "what should I do", name one concrete next step they can \
start today, and say what it changes.\
""",
    Role.EMPLOYER: """\
You are talking to an employer about their own vacancies, courses and the \
candidates matched to them. Candidates are anonymous until they apply: refer \
to them by their match score and skills, never by name, age or contact.\
""",
    Role.ADMIN: """\
You are talking to a platform administrator. They see aggregates and \
moderation queues; give them the shape of the numbers, not a story about one \
person.\
""",
}

# ---------------------------------------------------------------------------
# Where they are
#
# Derived from what the account actually contains rather than asked for. A
# stage is a claim about somebody's situation, and the database already knows.
# ---------------------------------------------------------------------------
STAGE_BLOCKS = {
    "new": """\
This person has just arrived and has almost nothing on their profile yet. \
Assume no familiarity with the platform's vocabulary — explain a term the \
first time you use it. Give them one step, not a plan; a list of six things is \
how a new person closes the tab.\
""",
    "starting": """\
This person has begun but has little evidence yet. Point at the shortest route \
to a confirmed skill, and be concrete about what "confirmed" means here: a \
test result or an employer's verification, not a self-rating.\
""",
    "building": """\
This person is well into their track. They know how the platform works, so \
skip the explanations of it and talk about the substance. Where a number of \
theirs is weak, say which one and what moves it.\
""",
    "ready": """\
This person has real evidence behind them and is close to applying for work. \
Talk to them about matching, gaps that still cost them points, and how a \
particular employer reads their profile. They do not need encouragement, they \
need specifics.\
""",
}


def stage_for(user) -> str:
    """Which of the four stages this account is at.

    Counted from evidence, not from a self-assessment. The thresholds are
    deliberately low: the difference that matters is between "has nothing to
    reason about" and "has something", not between good and excellent.
    """
    from apps.common.enums import EvidenceSource
    from apps.knowledge.models import KnowledgeScore
    from apps.learning.models import Enrollment, LessonProgress
    from apps.profiles.models import UserSkill

    # `best_source` rather than a raw count: a skill somebody typed in about
    # themselves is not evidence, and counting it here would put a brand-new
    # account with an optimistic self-assessment in the "ready" stage.
    confirmed = (
        UserSkill.objects.filter(user=user)
        .exclude(best_source=EvidenceSource.SELF)
        .count()
    )
    # Through the enrolment: LessonProgress belongs to an enrolment, not to a
    # user. Filtering it by `user` raises rather than returning nothing, which
    # is the good outcome — it failed loudly the first time it ran.
    lessons = LessonProgress.objects.filter(
        enrollment__user=user, status="COMPLETED"
    ).count()
    enrolments = Enrollment.objects.filter(user=user).count()
    knowledge = KnowledgeScore.objects.filter(user=user).count()

    if confirmed >= 5 and lessons >= 10:
        return "ready"
    if confirmed >= 1 or lessons >= 3:
        return "building"
    if enrolments or knowledge or lessons:
        return "starting"
    return "new"


# ---------------------------------------------------------------------------
# What they asked for
# ---------------------------------------------------------------------------
DETAIL_BLOCKS = {
    "BRIEF": "Answer in one or two sentences. No preamble.",
    "NORMAL": "Two to five sentences unless they asked for detail. No preamble.",
    "DETAILED": (
        "Give a full answer: the reasoning as well as the conclusion, and an "
        "example where one helps. Still no preamble."
    ),
}

TONE_BLOCKS = {
    "WARM": (
        "Be encouraging, but only about things the facts actually show. Praise "
        "for work that did not happen teaches them not to trust you."
    ),
    "NEUTRAL": "Be plain and matter-of-fact. Neither cheerful nor stern.",
    "DIRECT": (
        "Be brief and direct. Lead with the answer; skip the softening. If the "
        "news is bad, say it in the first sentence."
    ),
}

EXPLAIN_TERMS = (
    "Explain a platform term the first time it appears in your answer — "
    '"capital axis", "confirmed skill", "match score" — in half a sentence.'
)


def default_preferences(stage: str) -> dict:
    """What to assume before anybody has chosen anything.

    Derived from the stage so the default is already close: someone new gets
    the fuller, warmer answer that a first visit needs, and someone with a
    track record gets the short one. That makes the setting a correction for
    the people who want something else, rather than a form everybody has to
    fill in before the assistant is any use.
    """
    if stage in {"new", "starting"}:
        return {"detail": "DETAILED", "tone": "WARM", "explain_terms": True}
    if stage == "building":
        return {"detail": "NORMAL", "tone": "NEUTRAL", "explain_terms": False}
    return {"detail": "NORMAL", "tone": "DIRECT", "explain_terms": False}


def build_system_prompt(user, *, language: str, preferences: dict | None = None) -> str:
    """The whole system prompt for one person, one request.

    Composed rather than templated: each block is a paragraph that stands on
    its own, so a prompt that goes wrong can be read and the offending block
    found, instead of a single string with six conditionals inside it.
    """
    stage = stage_for(user) if user is not None else "building"
    prefs = {**default_preferences(stage), **(preferences or {})}

    parts = [BASE]

    role_block = ROLE_BLOCKS.get(getattr(user, "role", None))
    if role_block:
        parts.append(role_block)

    # The stage block is for the person's own situation. An employer's stage
    # is meaningless — their account has no lessons or skills — so it is only
    # added for the roles where it describes something real.
    if getattr(user, "role", None) == Role.STUDENT:
        parts.append(STAGE_BLOCKS[stage])

    parts.append(DETAIL_BLOCKS.get(prefs["detail"], DETAIL_BLOCKS["NORMAL"]))
    parts.append(TONE_BLOCKS.get(prefs["tone"], TONE_BLOCKS["NEUTRAL"]))
    if prefs.get("explain_terms"):
        parts.append(EXPLAIN_TERMS)

    parts.append(
        f"Write in {language}. Use no bullet lists unless you are enumerating "
        f"things that are genuinely a list."
    )
    return "\n\n".join(parts)
