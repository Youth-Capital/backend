"""Running an intake interview and turning it into a profile.

The write step is deliberately conservative. Everything a person types about
themselves lands as **self-declared** evidence (0.35 confidence), never as
anything stronger — an interview is a claim, not a demonstration. What it buys
is a starting point: a target profession, a first skill list, and enough
context for the plan and the recommender to say something specific instead of
greeting an empty dashboard.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.common.enums import EvidenceSource
from apps.profiles.models import EducationStatus, StudentProfile
from apps.taxonomy.models import Profession, Skill, SkillCategory

from .intake import (
    BY_ID,
    InterviewState,
    question_payload,
    validate_answer,
)
from .models import IntakeSession, IntakeStatus

#: Interview answer → profile column. Only statuses the profile actually has.
EDUCATION_MAP = {
    "SCHOOL": EducationStatus.SCHOOL,
    "COLLEGE": EducationStatus.COLLEGE,
    "UNIVERSITY": EducationStatus.UNIVERSITY,
    "GRADUATE": EducationStatus.GRADUATE,
    "NONE": EducationStatus.NONE,
}


def get_or_start(user) -> IntakeSession:
    """The learner's live interview, created on first ask.

    One active session per user is enforced by a database constraint, so a
    double-click on "start" cannot produce two half-finished interviews.
    """
    session = IntakeSession.objects.filter(
        user=user, status=IntakeStatus.IN_PROGRESS
    ).first()
    if session:
        return session

    from .services import get_ai_service

    return IntakeSession.objects.create(
        user=user, provider=get_ai_service().provider
    )


def state_for(session: IntakeSession) -> InterviewState:
    return InterviewState(answers=dict(session.answers or {}))


def describe(session: IntakeSession) -> dict:
    """Everything the client needs to render the current step."""
    state = state_for(session)
    question = state.next_question()
    answered, total = state.progress()

    payload = None
    if question is not None:
        professions = None
        if question.id == "target_profession":
            professions = Profession.objects.filter(is_active=True).order_by("name_uz")
        payload = question_payload(question, profession_choices=professions)

    return {
        "session_id": str(session.id),
        "status": session.status,
        "question": payload,
        "answered": answered,
        "total": total,
        "answers": session.answers,
        "applied": session.applied,
    }


@transaction.atomic
def submit(session: IntakeSession, question_id: str, value) -> IntakeSession:
    """Record one answer, validating it against that question's own rules."""
    question = BY_ID.get(question_id)
    if question is None:
        raise ValueError("unknown_question")

    state = state_for(session)
    if not question.is_applicable(state.answers):
        # Answering a branch that does not apply would poison later branching.
        raise ValueError("question_not_applicable")

    cleaned = validate_answer(question, value)

    answers = dict(session.answers or {})
    answers[question_id] = cleaned
    session.answers = answers
    session.save(update_fields=["answers", "updated_at"])
    return session


def skip(session: IntakeSession, question_id: str) -> IntakeSession:
    """Explicitly pass on an optional question so the interview can move on."""
    question = BY_ID.get(question_id)
    if question is None:
        raise ValueError("unknown_question")
    if question.required:
        raise ValueError("question_required")

    answers = dict(session.answers or {})
    answers[question_id] = [] if question.kind in {"MULTI", "SKILLS"} else ""
    session.answers = answers
    session.save(update_fields=["answers", "updated_at"])
    return session


@transaction.atomic
def complete(session: IntakeSession) -> IntakeSession:
    """Write the interview onto the profile and close it."""
    state = state_for(session)
    if not state.is_complete():
        raise ValueError("interview_incomplete")

    profile = StudentProfile.objects.filter(user=session.user).first()
    if profile is None:
        raise ValueError("no_student_profile")

    answers = state.answers
    applied: dict = {}

    # -- plain profile columns --------------------------------------------
    education = answers.get("education_status")
    if education in EDUCATION_MAP:
        profile.education_status = EDUCATION_MAP[education]
        applied["education_status"] = profile.education_status

    institution = (answers.get("institution") or "").strip()
    if institution:
        profile.institution = institution[:200]
        applied["institution"] = profile.institution

    about = (answers.get("about") or "").strip()
    if about and not profile.bio:
        # Only fills an empty bio — never overwrites something the person
        # already wrote about themselves.
        profile.bio = about[:2000]
        applied["bio"] = True

    target_id = answers.get("target_profession")
    if target_id:
        profession = Profession.objects.filter(id=target_id).first()
        if profession is not None:
            profile.target_profession = profession
            applied["target_profession"] = profession.name

    profile.save()

    # -- interests ---------------------------------------------------------
    slugs = answers.get("interests") or []
    if slugs:
        categories = list(SkillCategory.objects.filter(slug__in=slugs))
        profile.interests.set(categories)
        applied["interests"] = [category.slug for category in categories]

    # -- skills, as self-declared evidence ---------------------------------
    applied["skills"] = _record_skills(session, answers.get("skills") or [])

    # -- suggestion when the person does not know what they want -----------
    if answers.get("knows_profession") == "NO":
        applied["suggested_professions"] = suggest_professions(session.user, slugs)

    session.status = IntakeStatus.COMPLETED
    session.completed_at = timezone.now()
    session.applied = applied
    session.save(update_fields=["status", "completed_at", "applied", "updated_at"])

    # One recompute at the end rather than per skill: the chain is
    # skill → knowledge → capital → matches, and running it seven times for a
    # seven-skill interview would do the same work seven times.
    from apps.common.recompute import schedule_recompute

    schedule_recompute(session.user)

    return session


def _record_skills(session, entries: list[dict]) -> list[str]:
    """Write declared skills as SELF evidence, tagged with this interview.

    The reference matters: without ref_type *and* ref_id the evidence row is
    stored untagged, so re-running an interview could not update its own
    earlier claims and would instead sit next to them.
    """
    from apps.profiles.services import record_skill_evidence

    recorded = []
    for entry in entries:
        skill = Skill.objects.filter(id=entry.get("skill")).first()
        if skill is None:
            continue
        record_skill_evidence(
            user=session.user,
            skill=skill,
            source=EvidenceSource.SELF,
            score=entry.get("level", 40),
            ref_type="IntakeSession",
            ref_id=session.id,
            note="Declared during intake interview",
        )
        recorded.append(skill.slug)
    return recorded


def suggest_professions(user, interest_slugs: list[str], limit: int = 3) -> list[dict]:
    """Professions worth looking at when the answer was "I'm not sure".

    Ranked by how much of the profession's required-skill set sits inside the
    categories the person said interested them. Crude on purpose: this is a
    conversation starter shown as suggestions, not the matching engine.
    """
    if not interest_slugs:
        return []

    professions = (
        Profession.objects.filter(is_active=True)
        .prefetch_related("skill_links__skill__category")
        .distinct()
    )

    scored = []
    for profession in professions:
        links = list(profession.skill_links.all())
        if not links:
            continue
        hits = sum(
            1
            for link in links
            if link.skill.category and link.skill.category.slug in interest_slugs
        )
        if hits:
            scored.append(
                {
                    "id": str(profession.id),
                    "name": profession.name,
                    "overlap": round(hits / len(links) * 100),
                }
            )

    scored.sort(key=lambda row: row["overlap"], reverse=True)
    return scored[:limit]
