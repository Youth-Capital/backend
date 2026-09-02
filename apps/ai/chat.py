"""Grounded chat.

The point of this module is what it *refuses* to do. It never computes a
number and never phrases a claim from nothing. Every answer is:

    question → intent → the exact rows that intent needs → answer code + facts

The client renders the code into Uzbek, Russian or English, exactly as it
already does for match explanations. So "why is my score 87%?" is answered by
reading the stored breakdown, not by describing what a score usually depends
on — which is the only way the answer can be trusted or audited.

When an LLM provider is configured it receives the same grounding and is asked
to *phrase* it. It is never asked to calculate. If the model and the engine
ever disagreed, the engine is what the employer sees on the candidate list,
so the engine is what the chat must explain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from apps.common.enums import Role


# --------------------------------------------------------------------------
# Intents
# --------------------------------------------------------------------------
class Intent:
    MATCH_EXPLAIN = "MATCH_EXPLAIN"
    SKILL_EXPLAIN = "SKILL_EXPLAIN"
    CAPITAL_EXPLAIN = "CAPITAL_EXPLAIN"
    KNOWLEDGE_EXPLAIN = "KNOWLEDGE_EXPLAIN"
    GAP = "GAP"
    VERIFY = "VERIFY"
    PROFESSIONS = "PROFESSIONS"
    COURSES = "COURSES"
    NEXT_STEP = "NEXT_STEP"
    PROGRESS = "PROGRESS"

    CANDIDATE_EXPLAIN = "CANDIDATE_EXPLAIN"
    VACANCY_STATS = "VACANCY_STATS"
    MATCH_METHOD = "MATCH_METHOD"

    #: The catch-all answer: where this person stands, for a question the
    #: classifier could not place.
    BRIEFING = "BRIEFING"
    HELP = "HELP"
    UNKNOWN = "UNKNOWN"


@dataclass
class Answer:
    """What the assistant replies with.

    `code` selects the sentence; `facts` are the values that go into it and the
    receipts the UI shows underneath. `sources` names where each number was
    read from, so a sceptical user can go and look.
    """

    code: str
    intent: str
    facts: dict[str, Any] = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Classification
#
# Keywords in all three languages. Deliberately shallow: a wrong guess costs a
# "did you mean" reply, whereas a clever classifier that silently answers the
# wrong question costs trust.
# --------------------------------------------------------------------------
KEYWORDS: dict[str, tuple[str, ...]] = {
    Intent.MATCH_EXPLAIN: (
        "моснад", "moslash", "match", "процент", "foiz", "совпаден", "подход",
        "почему такой балл", "why this score", "ball qayerdan",
    ),
    Intent.SKILL_EXPLAIN: (
        "навык", "ko'nikma", "konikma", "skill", "уровень", "daraja", "level",
        "подтвержд", "tasdiq", "verified", "откуда", "qayerdan",
    ),
    Intent.CAPITAL_EXPLAIN: ("капитал", "kapital", "capital", "индекс", "indeks", "index"),
    Intent.KNOWLEDGE_EXPLAIN: ("знан", "bilim", "knowledge", "тест", "test", "балл знан"),
    Intent.GAP: (
        "не хватает", "yetishmayapti", "yetishmaydi", "missing", "gap", "разрыв",
        "чего не", "nima kerak", "what do i need",
    ),
    # Verifying is the platform's whole middle step — learn, prove, get hired —
    # and it was the one thing the assistant could not be asked about.
    Intent.VERIFY: (
        "проверить знания", "проверить свои знания", "подтвердить навык",
        "подтвердить знания", "сдать тест", "пройти тест", "где тест",
        "как подтвердить", "доказать",
        "bilimni tekshirish", "tasdiqlash", "test topshirish",
        "verify my skills", "prove my skills", "take a test",
    ),
    Intent.NEXT_STEP: (
        "что дальше", "nima qilay", "next", "следующий шаг", "keyingi qadam",
        "что делать", "what should i", "с чего начать",
    ),
    Intent.PROGRESS: ("прогресс", "progress", "как мои", "qanday ketyapti", "how am i"),
    Intent.CANDIDATE_EXPLAIN: (
        "кандидат", "nomzod", "candidate", "почему он", "почему она", "nega bu",
    ),
    Intent.VACANCY_STATS: ("ваканс", "vakansiya", "vacancy", "отклик", "ariza", "application"),
    Intent.MATCH_METHOD: (
        "как вы счита", "qanday hisoblan", "how do you calculate", "формула",
        "formula", "методика", "metodika", "как считается", "как посчитал",
    ),
    # What the platform actually offers. Basic questions that had no answer
    # at all, so they fell through to a briefing about the person instead.
    Intent.PROFESSIONS: (
        "какие профессии", "какие специальности", "кем можно стать",
        "профессии есть", "список профессий", "что можно изучать",
        "qanday kasblar", "kasblar ro'yxati", "what professions", "which careers",
    ),
    Intent.COURSES: (
        "какие курсы", "курсы есть", "список курсов", "чему можно научиться",
        "qanday kurslar", "kurslar ro'yxati", "what courses", "which courses",
    ),
    Intent.HELP: ("помощь", "yordam", "help", "что ты умеешь", "nima qila olasan", "what can you"),
}

#: Which intents each role may reach. A student asking about "candidates"
#: means their own standing, not somebody else's profile.
ROLE_INTENTS = {
    Role.STUDENT: {
        Intent.MATCH_EXPLAIN, Intent.SKILL_EXPLAIN, Intent.CAPITAL_EXPLAIN,
        Intent.KNOWLEDGE_EXPLAIN, Intent.GAP, Intent.VERIFY, Intent.NEXT_STEP,
        Intent.PROGRESS, Intent.MATCH_METHOD,
        Intent.PROFESSIONS, Intent.COURSES, Intent.HELP,
    },
    Role.EMPLOYER: {
        Intent.CANDIDATE_EXPLAIN, Intent.VACANCY_STATS,
        Intent.MATCH_METHOD, Intent.PROFESSIONS, Intent.COURSES, Intent.HELP,
    },
}


#: Asked *about* something specific. These outrank the generic
#: "how do you calculate this" intent, which is only a fallback for a question
#: that names no subject — otherwise "как считается индекс капитала" answers
#: with the matching formula, because that phrase happens to be longer.
GENERIC_INTENTS = {Intent.MATCH_METHOD, Intent.HELP}

#: Enough of a word to survive Russian and Uzbek inflection. "начать",
#: "начну" and "boshla", "boshlash" all share their first five characters,
#: which is the whole reason this is a prefix comparison and not equality.
STEM = 5


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w'-]+", text.lower()) if t]


def _phrase_matches(phrase_tokens: list[str], text_tokens: list[str]) -> bool:
    """Do the phrase's words appear in order, allowing anything between them?

    Substring matching failed on ordinary speech: the keyword "с чего начать"
    misses "с чего мне стоит начать", because two inserted words break the
    string even though the question is identical. Matching in order with gaps
    accepts the way people actually write.
    """
    position = 0
    for wanted in phrase_tokens:
        stem = wanted[:STEM]
        while position < len(text_tokens):
            if text_tokens[position][:STEM] == stem:
                position += 1
                break
            position += 1
        else:
            return False
    return True


def classify(text: str, role: str) -> str:
    """Best-matching intent, or UNKNOWN.

    A named subject always beats a generic "how is this calculated", and among
    equals the longest phrase wins — a more specific keyword is a stronger
    signal than a single common word.
    """
    text_tokens = _tokens(text)
    allowed = ROLE_INTENTS.get(role, set())

    def best_of(intents: set[str]) -> tuple[str, int]:
        found, score = Intent.UNKNOWN, 0
        for intent in intents:
            for phrase in KEYWORDS.get(intent, ()):
                phrase_tokens = _tokens(phrase)
                if not phrase_tokens:
                    continue
                if _phrase_matches(phrase_tokens, text_tokens):
                    # Longer phrases score higher; ties keep the first found.
                    weight = sum(len(t) for t in phrase_tokens) + len(phrase_tokens)
                    if weight > score:
                        found, score = intent, weight
        return found, score

    subject, _ = best_of(allowed - GENERIC_INTENTS)
    if subject != Intent.UNKNOWN:
        return subject

    generic, _ = best_of(allowed & GENERIC_INTENTS)
    return generic


# --------------------------------------------------------------------------
# Answers — each one reads real rows
# --------------------------------------------------------------------------
def _components(result) -> dict:
    """The six scores, read from the columns that actually hold them.

    `breakdown` carries the *weights* plus per-component diagnostics, not the
    scores — passing it off as the components produced receipts reading
    "skills_required 2%", which are counts, not percentages.
    """
    return {
        "coverage": result.coverage_score,
        "knowledge": result.knowledge_score,
        "verification": result.verification_score,
        "experience": result.experience_score,
        "education": result.education_score,
        "location": result.location_score,
    }


def _match_explain(user) -> Answer:
    from apps.matching.models import MatchResult

    best = (
        MatchResult.objects.filter(student=user)
        .select_related("vacancy")
        .order_by("-overall_score")
        .first()
    )
    if best is None:
        return Answer("match.none", Intent.MATCH_EXPLAIN)

    return Answer(
        code="match.explain",
        intent=Intent.MATCH_EXPLAIN,
        facts={
            "vacancy": best.vacancy.title,
            "overall": best.overall_score,
            "components": _components(best),
            "detail": best.breakdown or {},
            "reasons": best.explanation or [],
        },
        sources=[{"type": "MatchResult", "id": str(best.id), "screen": "/student/jobs"}],
        suggestions=[Intent.MATCH_METHOD, Intent.GAP],
    )


def _skill_explain(user) -> Answer:
    from apps.profiles.models import UserSkill

    skills = list(
        UserSkill.objects.filter(user=user)
        .select_related("skill")
        .prefetch_related("evidence")
        .order_by("-proficiency")[:5]
    )
    if not skills:
        return Answer("skill.none", Intent.SKILL_EXPLAIN, suggestions=[Intent.NEXT_STEP])

    rows = []
    for user_skill in skills:
        evidence = [
            {
                "source": item.source,
                "score": item.score,
                "weight": float(item.weight),
                "note": item.note,
            }
            for item in user_skill.evidence.all()
        ]
        rows.append(
            {
                "skill": user_skill.skill.name,
                "proficiency": user_skill.proficiency,
                "confidence": user_skill.confidence,
                "status": user_skill.status,
                "evidence": evidence,
            }
        )

    return Answer(
        code="skill.explain",
        intent=Intent.SKILL_EXPLAIN,
        facts={"skills": rows},
        sources=[{"type": "SkillEvidence", "screen": "/student/skills"}],
        suggestions=[Intent.GAP],
    )


def _capital_explain(user) -> Answer:
    from apps.capital.services import get_capital_overview

    overview = get_capital_overview(user)

    return Answer(
        code="capital.explain",
        intent=Intent.CAPITAL_EXPLAIN,
        facts={
            "overall": overview.get("overall", 0),
            # The service reports these directly; deriving them from a list
            # named "axes" produced "0 of 0" next to a non-zero index.
            "measured": overview.get("measured_axes", 0),
            "total": overview.get("total_axes", 0),
            "axes": overview.get("dimensions", []),
        },
        sources=[{"type": "CapitalIndex", "screen": "/student/dashboard"}],
    )


def _knowledge_explain(user) -> Answer:
    from apps.knowledge.services import get_knowledge_overview

    overview = get_knowledge_overview(user, limit=8)
    return Answer(
        code="knowledge.explain",
        intent=Intent.KNOWLEDGE_EXPLAIN,
        facts=overview,
        sources=[{"type": "KnowledgeScore", "screen": "/student/knowledge"}],
    )


def _gap(user) -> Answer:
    profile = getattr(user, "student_profile", None)
    target = getattr(profile, "target_profession", None) if profile else None
    if target is None:
        return Answer("gap.no_target", Intent.GAP, suggestions=[Intent.NEXT_STEP])

    from .services import get_ai_service

    report = get_ai_service().analyze_skills(user, target)
    return Answer(
        code="gap.explain",
        intent=Intent.GAP,
        facts={
            "profession": report.profession,
            "readiness": report.readiness,
            "matching": report.matching,
            "partial": report.partial,
            "missing": report.missing,
            "next_actions": report.next_actions,
        },
        sources=[{"type": "Profession", "id": str(target.id), "screen": "/student/career"}],
        suggestions=[Intent.NEXT_STEP],
    )


def _verify(user) -> Answer:
    """How to turn a declared skill into a proven one.

    Answers the middle of the whole promise: a self-declared skill weighs 0.35
    and a passed test 0.90, so "how do I prove this" is the highest-leverage
    question a learner can ask — and the tests that would move *their* skills
    are the only useful form of the answer.
    """
    from apps.assessment.models import Test, TestAttempt
    from apps.common.enums import ModerationStatus
    from apps.profiles.models import UserSkill

    # Skills the person claims but has not proven: exactly what a test fixes.
    unproven = list(
        UserSkill.objects.filter(user=user)
        .exclude(status="VERIFIED")
        .select_related("skill")
        .order_by("-proficiency")[:8]
    )
    unproven_ids = {row.skill_id for row in unproven}

    taken = set(
        TestAttempt.objects.filter(user=user).values_list("test_id", flat=True)
    )

    tests = (
        Test.objects.filter(status=ModerationStatus.PUBLISHED)
        .prefetch_related("skill_links__skill")
        .distinct()
    )

    relevant, other = [], []
    for test in tests:
        skills = [link.skill for link in test.skill_links.all()]
        row = {
            "id": str(test.id),
            "title": test.title,
            "skills": [skill.name for skill in skills[:4]],
            "taken": test.id in taken,
        }
        if any(skill.id in unproven_ids for skill in skills):
            relevant.append(row)
        else:
            other.append(row)

    return Answer(
        code="verify.explain",
        intent=Intent.VERIFY,
        facts={
            "unproven": [
                {"skill": row.skill.name, "level": row.proficiency, "status": row.status}
                for row in unproven[:5]
            ],
            "tests": (relevant + other)[:6],
            "weight_self": float(0.35),
            "weight_test": float(0.90),
        },
        sources=[{"type": "Test", "screen": "/student/tests"}],
        suggestions=[Intent.GAP, Intent.SKILL_EXPLAIN],
    )


def _next_step(user) -> Answer:
    from apps.idp.services import get_today_tasks

    tasks = get_today_tasks(user, limit=3)
    rows = [
        {
            "title": getattr(task, "title", ""),
            "due": task.due_date.isoformat() if getattr(task, "due_date", None) else None,
            "status": getattr(task, "status", ""),
        }
        for task in tasks
    ]
    if not rows:
        return Answer("next.no_plan", Intent.NEXT_STEP, suggestions=[Intent.GAP])

    return Answer(
        code="next.tasks",
        intent=Intent.NEXT_STEP,
        facts={"tasks": rows},
        sources=[{"type": "Task", "screen": "/student/plan"}],
    )


def _progress(user) -> Answer:
    from apps.capital.services import get_capital_overview
    from apps.learning.models import Enrollment, EnrollmentStatus
    from apps.profiles.models import UserSkill

    skills = UserSkill.objects.filter(user=user)
    return Answer(
        code="progress.summary",
        intent=Intent.PROGRESS,
        facts={
            "skills_total": skills.count(),
            "skills_verified": skills.filter(status="VERIFIED").count(),
            "courses_completed": Enrollment.objects.filter(
                user=user, status=EnrollmentStatus.COMPLETED
            ).count(),
            "capital": get_capital_overview(user).get("overall", 0),
        },
        sources=[{"type": "Dashboard", "screen": "/student/dashboard"}],
        suggestions=[Intent.NEXT_STEP],
    )


def _candidate_explain(user) -> Answer:
    from apps.common.enums import ModerationStatus
    from apps.matching.models import MatchResult

    best = (
        MatchResult.objects.filter(
            vacancy__employer__owner=user,
            vacancy__status=ModerationStatus.PUBLISHED,
        )
        .select_related("vacancy")
        .order_by("-overall_score")
        .first()
    )
    if best is None:
        return Answer("candidate.none", Intent.CANDIDATE_EXPLAIN)

    # Names are not returned: the employer sees a candidate's identity only
    # after they apply (TZ §12), and the chat must not become a way around it.
    return Answer(
        code="candidate.explain",
        intent=Intent.CANDIDATE_EXPLAIN,
        facts={
            "vacancy": best.vacancy.title,
            "overall": best.overall_score,
            "components": _components(best),
            "detail": best.breakdown or {},
            "reasons": best.explanation or [],
        },
        sources=[
            {
                "type": "MatchResult",
                "screen": f"/employer/vacancies/{best.vacancy_id}/candidates",
            }
        ],
        suggestions=[Intent.MATCH_METHOD],
    )


def _vacancy_stats(user) -> Answer:
    from apps.common.enums import ModerationStatus
    from apps.jobs.models import Application, Vacancy

    vacancies = Vacancy.objects.filter(employer__owner=user)
    published = vacancies.filter(status=ModerationStatus.PUBLISHED)
    applications = Application.objects.filter(vacancy__employer__owner=user)

    return Answer(
        code="vacancy.stats",
        intent=Intent.VACANCY_STATS,
        facts={
            "total": vacancies.count(),
            "published": published.count(),
            "applications": applications.count(),
            "new": applications.filter(status="APPLIED").count(),
        },
        sources=[{"type": "Vacancy", "screen": "/employer/vacancies"}],
    )


def _match_method(user) -> Answer:
    """How the score is built — read from the live weight profile, not prose.

    Quoting the numbers actually in use means this answer cannot drift out of
    date when someone tunes the weights.
    """
    from apps.matching.models import MatchWeightProfile

    # `normalised_weights()` is what the engine itself uses, including its
    # guard for a profile whose weights do not sum to 1. Reading the raw
    # column instead would describe a calculation nobody actually ran.
    weights = MatchWeightProfile.active().normalised_weights()

    return Answer(
        code="method.explain",
        intent=Intent.MATCH_METHOD,
        facts={
            "weights": weights,
            "evidence_weights": {
                "SELF": 0.35, "COURSE": 0.65, "EXPERIENCE": 0.70,
                "MENTOR": 0.85, "TEST": 0.90, "EMPLOYER": 1.00,
            },
        },
        sources=[{"type": "MatchWeightProfile", "screen": ""}],
    )


def _student_briefing(user) -> Answer:
    """Everything known about this learner, in one answer.

    The fallback for a question the classifier could not place. "I did not
    understand, pick from the list" is the worst available reply: it makes the
    reader do the assistant's job. A short account of where they actually
    stand answers most open questions incidentally — where am I, what is
    missing, what now — and reads the same rows as every other answer.
    """
    from apps.capital.services import get_capital_overview
    from apps.learning.models import Enrollment, EnrollmentStatus
    from apps.matching.models import MatchResult
    from apps.profiles.models import UserSkill

    profile = getattr(user, "student_profile", None)
    target = getattr(profile, "target_profession", None) if profile else None

    skills = UserSkill.objects.filter(user=user)
    capital = get_capital_overview(user)
    best = (
        MatchResult.objects.filter(student=user)
        .select_related("vacancy")
        .order_by("-overall_score")
        .first()
    )

    readiness, missing = None, []
    if target is not None:
        from .services import get_ai_service

        report = get_ai_service().analyze_skills(user, target)
        readiness, missing = report.readiness, report.missing[:3]

    try:
        from apps.idp.services import get_today_tasks

        # The same shape `_next_step` sends. Two shapes behind one field name
        # rendered as "1." and "2." with no text next to them.
        tasks = [
            {
                "title": getattr(t, "title", ""),
                "due": t.due_date.isoformat() if getattr(t, "due_date", None) else None,
            }
            for t in get_today_tasks(user, limit=2)
        ]
    except Exception:
        # A missing plan must not turn a briefing into an error page.
        tasks = []

    return Answer(
        code="briefing.student",
        intent=Intent.BRIEFING,
        facts={
            "profession": target.name if target else "",
            "readiness": readiness,
            "skills_total": skills.count(),
            "skills_verified": skills.filter(status="VERIFIED").count(),
            "courses_completed": Enrollment.objects.filter(
                user=user, status=EnrollmentStatus.COMPLETED
            ).count(),
            "capital": capital.get("overall", 0),
            "capital_measured": capital.get("measured_axes", 0),
            "capital_total": capital.get("total_axes", 0),
            "best_match": best.overall_score if best else None,
            "best_vacancy": best.vacancy.title if best else "",
            "missing": missing,
            "tasks": tasks,
        },
        sources=[{"type": "Dashboard", "screen": "/student/dashboard"}],
        suggestions=[Intent.GAP, Intent.NEXT_STEP, Intent.MATCH_METHOD],
    )


def _employer_briefing(user) -> Answer:
    """The same idea for the hiring side."""
    from apps.common.enums import ModerationStatus
    from apps.jobs.models import Application, Vacancy
    from apps.matching.models import MatchResult

    vacancies = Vacancy.objects.filter(employer__owner=user)
    applications = Application.objects.filter(vacancy__employer__owner=user)

    return Answer(
        code="briefing.employer",
        intent=Intent.BRIEFING,
        facts={
            "published": vacancies.filter(status=ModerationStatus.PUBLISHED).count(),
            "total": vacancies.count(),
            "applications": applications.count(),
            "new": applications.filter(status="APPLIED").count(),
            "strong_matches": MatchResult.objects.filter(
                vacancy__employer__owner=user,
                vacancy__status=ModerationStatus.PUBLISHED,
                overall_score__gte=60,
            ).count(),
        },
        sources=[{"type": "Vacancy", "screen": "/employer/vacancies"}],
        suggestions=[Intent.CANDIDATE_EXPLAIN, Intent.MATCH_METHOD],
    )


def _professions(user) -> Answer:
    """The catalogue, with the reader's own readiness against each one.

    "What can I study here" is the most basic question a visitor has, and a
    bare list answers it badly. Marking the target profession and showing how
    many required skills are already covered turns a catalogue into a
    comparison.
    """
    from apps.taxonomy.models import Profession
    from apps.profiles.models import UserSkill

    profile = getattr(user, "student_profile", None)
    target_id = getattr(profile, "target_profession_id", None) if profile else None

    have = {
        row.skill_id: row.proficiency
        for row in UserSkill.objects.filter(user=user).only("skill_id", "proficiency")
    }

    rows = []
    professions = (
        Profession.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related("skill_links")
        .order_by("name_uz")[:20]
    )
    for profession in professions:
        links = [
            link
            for link in profession.skill_links.all()
            if link.requirement == "REQUIRED"
        ]
        met = sum(
            1 for link in links if have.get(link.skill_id, 0) >= link.min_proficiency
        )
        rows.append(
            {
                "id": str(profession.id),
                "name": profession.name,
                "category": profession.category.name if profession.category_id else "",
                "demand": profession.demand_level,
                "required": len(links),
                "met": met,
                "is_target": str(profession.id) == str(target_id),
            }
        )

    return Answer(
        code="professions.list",
        intent=Intent.PROFESSIONS,
        facts={"professions": rows, "count": len(rows)},
        sources=[{"type": "Profession", "screen": "/student/career"}],
        suggestions=[Intent.GAP, Intent.COURSES],
    )


def _courses(user) -> Answer:
    """Published courses, with what each one teaches."""
    from apps.common.enums import ModerationStatus
    from apps.learning.models import Course, Enrollment

    enrolled = set(
        Enrollment.objects.filter(user=user).values_list("course_id", flat=True)
    )

    rows = []
    for course in (
        Course.objects.filter(status=ModerationStatus.PUBLISHED)
        .prefetch_related("skill_links__skill")
        .order_by("title")[:20]
    ):
        rows.append(
            {
                "id": str(course.id),
                "title": course.title,
                "skills": [link.skill.name for link in course.skill_links.all()[:4]],
                "enrolled": course.id in enrolled,
            }
        )

    return Answer(
        code="courses.list",
        intent=Intent.COURSES,
        facts={"courses": rows, "count": len(rows)},
        sources=[{"type": "Course", "screen": "/student/courses"}],
        suggestions=[Intent.PROFESSIONS, Intent.VERIFY],
    )


def _help(user) -> Answer:
    return Answer(
        code="help.list",
        intent=Intent.HELP,
        facts={"intents": sorted(ROLE_INTENTS.get(user.role, set()) - {Intent.HELP})},
    )


HANDLERS: dict[str, Callable[[Any], Answer]] = {
    Intent.MATCH_EXPLAIN: _match_explain,
    Intent.SKILL_EXPLAIN: _skill_explain,
    Intent.CAPITAL_EXPLAIN: _capital_explain,
    Intent.KNOWLEDGE_EXPLAIN: _knowledge_explain,
    Intent.GAP: _gap,
    Intent.VERIFY: _verify,
    Intent.PROFESSIONS: _professions,
    Intent.COURSES: _courses,
    Intent.NEXT_STEP: _next_step,
    Intent.PROGRESS: _progress,
    Intent.CANDIDATE_EXPLAIN: _candidate_explain,
    Intent.VACANCY_STATS: _vacancy_stats,
    Intent.MATCH_METHOD: _match_method,
    Intent.HELP: _help,
}


def jsonable(value):
    """Coerce model values into something a JSONField can store.

    Applied to every answer rather than to the one field that broke: model
    columns are Decimals, dates and UUIDs all over this codebase, so a handler
    added later would otherwise reintroduce a 500 the same way. Grounding is
    persisted, so an untranslatable value fails the whole reply.
    """
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return value


def answer(user, text: str) -> Answer:
    """Classify, then read. Never invent.

    An unplaced question falls through to a briefing rather than a shrug. Most
    open questions — "с чего начать", "как у меня дела", "что вообще
    происходит" — are answered incidentally by an account of where the person
    stands, and "I did not understand, pick from the list" answers none of
    them while making the reader do the assistant's job.
    """
    intent = classify(text, user.role)
    handler = HANDLERS.get(intent)
    if handler is None:
        result = (
            _employer_briefing(user)
            if user.role == Role.EMPLOYER
            else _student_briefing(user)
        )
    else:
        result = handler(user)

    result.facts = jsonable(result.facts)
    result.sources = jsonable(result.sources)
    return result
