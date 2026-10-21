"""Writing the CV for the student, out of what the platform already knows.

Two ways in, and the student picks: write it themselves, or have this do a
first draft. The draft is never saved on their behalf — it comes back as text
in the form, where they can change every word before anything is stored. An
assistant that files a document under somebody's name is not an assistant.

WHERE THE WORDS COME FROM

Only from the student's own rows: skills with their verified level, experience
with its dates, courses they finished, certificates they hold. The same
assembly the finished CV uses (`build_cv_payload`), so a draft can never
mention something the document itself would not show, and nothing has to be
re-queried into a second definition of "what a CV contains".

Nothing is invented. A model is given those rows as material and asked to
phrase them; with no model configured — which is how the platform runs by
default — the same rows are phrased by the code below. Both paths produce a
draft from identical facts, so the feature works on a laptop with no API key
and gets better, not different, when a key is added.

WHY THE MODEL PATH IS FENCED

The material is the student's own writing: experience descriptions, portfolio
titles, a profile headline. That is user-provided text, and user-provided text
reaching a model alongside instructions is the injection the AI hardening work
closed. So it goes through `backends.run_task`, which keeps instructions in the
system turn and facts in fenced user blocks, and vets the reply before anyone
sees it.
"""

from __future__ import annotations

import json
import logging

from django.utils.translation import gettext as _
from django.utils.translation import override

from apps.ai.backends import get_chat_backend
from apps.ai import prompting

from .models import DEFAULT_SECTIONS, CVDocument
from .services import build_cv_payload

logger = logging.getLogger(__name__)

#: What the model is asked to do. Rules only — no data, no names, nothing the
#: student wrote. The facts arrive separately, fenced, as material.
CV_RULES = """
You are drafting the opening of a CV for the person whose record is supplied.

Write exactly two things, as JSON with the keys "headline" and "summary":

* "headline": one line, at most 90 characters. The role they are aiming at,
  and at most three of their strongest skills. No punctuation flourishes.
* "summary": 2 to 4 sentences, first person, at most 600 characters. What they
  can do, what they have finished, and what they are working toward.

Rules:

1. Use only what the supplied record contains. If it does not say how many
   years of experience they have, do not say. Inventing a qualification on
   somebody's CV is the worst thing you can do here.
2. Prefer skills the record marks as verified. Say "verified" only about those.
3. No superlatives the record cannot support: not "expert", not "extensive",
   unless the record carries a level that means it.
4. Write in {language}.
5. Plain sentences. No bullet points, no markdown, no headings.
6. Return only the JSON object, nothing around it.
""".strip()

#: Skills below this proficiency are not worth a line in a summary.
MENTIONABLE = 40
#: How many skills a headline may name before it stops being a headline.
HEADLINE_SKILLS = 3


def collect_evidence(user, *, profession=None, language: str = "") -> dict:
    """Everything a CV may be written from, for this user and nobody else.

    Built through an unsaved document rather than a second set of queries: the
    section list, the narrowing to the target profession and the privacy rules
    all live in `build_cv_payload`, and a draft that pulled its own rows would
    drift away from the document the student ends up with.
    """
    # Never saved — it exists to carry the section list into `build_cv_payload`.
    # The sections have to be set here rather than left empty: the model fills
    # them in on save, and this one is never saved, so an empty list would mean
    # an empty payload and a draft written from nothing.
    scratch = CVDocument(user=user, target_profession=profession)
    if language:
        scratch.language = language
    scratch.sections_config = DEFAULT_SECTIONS
    payload = build_cv_payload(scratch, identified=True)

    skills = payload.get("skills", [])
    return {
        "target_profession": profession.name if profession else "",
        "skills": [
            {
                "name": s["name"],
                "level": s["proficiency"],
                "verified": s["verified"],
            }
            for s in skills
            if s["proficiency"] >= MENTIONABLE
        ],
        "experience": [
            {
                "title": e["title"],
                "organization": e["organization"],
                "months": e["duration_months"],
                "skills": e["skills"],
            }
            for e in payload.get("experience", [])
        ],
        "courses": [
            {"title": c["title"], "provider": c["provider"]}
            for c in payload.get("courses", [])
        ],
        "certificates": [c["title"] for c in payload.get("certificates", [])],
        "projects": [
            {"title": p["title"], "skills": p["skills"]}
            for p in payload.get("projects", [])
        ],
        "education": [
            {"degree": e["degree"], "field": e["field_of_study"]}
            for e in payload.get("education", [])
        ],
    }


def has_enough(evidence: dict) -> bool:
    """Is there anything to write a CV out of?

    A draft assembled from an empty profile is two sentences of nothing, and
    the student reads it as the feature being useless rather than as their
    profile being empty. Better to say which it is.
    """
    return bool(
        evidence["skills"]
        or evidence["experience"]
        or evidence["courses"]
        or evidence["projects"]
    )


def _written_by_code(evidence: dict, *, language: str) -> dict:
    """The draft when no model is configured — the default on this platform.

    Deliberately plain. It states what the record holds and stops: a sentence
    per kind of evidence, in the student's language, with the numbers coming
    from the rows rather than from an adjective.
    """
    with override(language or "uz"):
        verified = [s for s in evidence["skills"] if s["verified"]]
        named = [s["name"] for s in (verified or evidence["skills"])][:HEADLINE_SKILLS]

        role = evidence["target_profession"]
        headline = " · ".join(part for part in [role, ", ".join(named)] if part)

        sentences: list[str] = []
        if named:
            sentences.append(
                _("I work with %(skills)s.") % {"skills": ", ".join(named)}
            )
        if verified:
            sentences.append(
                _("%(count)d of my skills are confirmed by platform assessments.")
                % {"count": len(verified)}
            )
        months = sum(e["months"] or 0 for e in evidence["experience"])
        if months:
            sentences.append(
                _("I have %(months)d months of practical experience.")
                % {"months": months}
            )
        if evidence["courses"]:
            sentences.append(
                _("I have completed %(count)d courses, including %(first)s.")
                % {
                    "count": len(evidence["courses"]),
                    "first": evidence["courses"][0]["title"],
                }
            )
        if role:
            sentences.append(_("I am aiming for the role of %(role)s.") % {"role": role})

        return {
            "headline": headline[:200],
            "summary": " ".join(sentences)[:2000],
            "source": "platform",
        }


def _written_by_model(evidence: dict, *, language: str, user) -> dict | None:
    """The draft when a provider is configured. None if one is not, or it failed."""
    backend = get_chat_backend()
    if backend is None or not backend.is_ready():
        return None

    try:
        reply = backend.run_task(
            instructions=CV_RULES.format(language=language or "Uzbek (latin script)"),
            material=[("the person's own record", json.dumps(evidence, ensure_ascii=False, default=str))],
            user=user,
        )
    except prompting.OutputRejected:
        # The reply recited its instructions or carried a link the record never
        # gave. Already on the safety queue; the student gets the plain draft.
        return None
    except Exception:  # pragma: no cover - a provider outage is not the caller's problem
        logger.exception("CV draft call failed for user %s", getattr(user, "id", None))
        return None

    try:
        parsed = json.loads(reply)
        headline = str(parsed["headline"]).strip()
        summary = str(parsed["summary"]).strip()
    except (ValueError, KeyError, TypeError):
        logger.warning("CV draft came back in a shape this cannot use")
        return None

    if not headline and not summary:
        return None
    return {
        "headline": headline[:200],
        "summary": summary[:2000],
        "source": "model",
    }


def draft_cv(user, *, profession=None, language: str = "") -> dict:
    """A first draft of the headline and summary, for the student to edit.

    Returns the draft and the evidence it was written from, because a student
    reading a sentence about themselves should be able to see which rows put it
    there — and because an empty draft is explained by an empty record.
    """
    evidence = collect_evidence(user, profession=profession, language=language)
    if not has_enough(evidence):
        return {
            "available": False,
            "reason": "no_evidence",
            "headline": "",
            "summary": "",
            "evidence": evidence,
        }

    draft = _written_by_model(evidence, language=language, user=user) or _written_by_code(
        evidence, language=language
    )
    return {"available": True, "reason": "", **draft, "evidence": evidence}
