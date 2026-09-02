"""A short recap of a lesson, for someone coming back to revise.

The honest constraint first, because it shapes everything else: **the platform
cannot hear a YouTube video.** It holds a link. There is no audio, no
transcript, and no way to obtain one without going and asking YouTube for it.

So a recap is built from text somebody actually wrote — the lesson body, and
the transcript field an author can paste (YouTube hands them one). If neither
exists there is nothing to summarise, and the endpoint says so rather than
letting a model invent a plausible summary of a video it never watched. A
confident, wrong recap of a security lesson is worse than no recap: the learner
revises the wrong thing and does not know it.

The recap is cached on the lesson because it does not change until the lesson
does, and a model call per learner per revision is a bill for the same
sentences over and over.
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

#: Below this there is nothing worth summarising — a title and one line is
#: already shorter than any recap of it would be.
MIN_SOURCE_CHARS = 400

#: What the model is asked to produce.
RECAP_PROMPT = """\
You are writing a revision recap for a learner who has already watched this \
lesson and wants to remember what was in it.

Work only from the LESSON TEXT below. Do not add facts that are not in it, do \
not invent examples, and do not fill gaps from your own knowledge — the reader \
will trust this as a record of what they were taught.

Produce:
1. Two or three sentences saying what the lesson was about.
2. Between three and six key points, each one line.
3. If the text names specific terms, commands or figures, keep them exact.

Write in {language}. No preamble, no closing remark.

LESSON: {title}

LESSON TEXT:
{body}
"""


def recap_source(lesson) -> str:
    """The text a recap may be built from, longest-first.

    The transcript is preferred: it is what was actually said in the video the
    learner watched, and the lesson body is often just a heading.
    """
    parts = []
    if lesson.transcript:
        parts.append(lesson.transcript.strip())
    if lesson.content:
        parts.append(lesson.content.strip())
    return "\n\n".join(part for part in parts if part)


def source_fingerprint(text: str) -> str:
    """Identifies the text a stored recap was made from."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def can_recap(lesson) -> tuple[bool, str]:
    """Whether a recap is possible, and the reason when it is not."""
    source = recap_source(lesson)
    if not source:
        return False, "no_text"
    if len(source) < MIN_SOURCE_CHARS:
        return False, "too_short"
    return True, ""


def build_recap(lesson, *, language: str = "Russian") -> str:
    """Ask the configured model to summarise the lesson's own text.

    Raises RuntimeError when there is no model configured — the caller turns
    that into an honest "not available", never into a fabricated summary.
    """
    from apps.ai.backends import get_chat_backend

    allowed, reason = can_recap(lesson)
    if not allowed:
        raise ValueError(reason)

    backend = get_chat_backend()
    if backend is None:
        raise RuntimeError("No model is configured.")

    source = recap_source(lesson)
    # Long transcripts are trimmed rather than refused: an hour of speech is
    # tens of thousands of characters, and the opening is where a lesson says
    # what it is about.
    body = source[:24_000]

    return backend.reply(
        question=RECAP_PROMPT.format(
            language=language, title=lesson.title, body=body
        ),
        facts={},
        history=[],
    ).strip()


# ---------------------------------------------------------------------------
# Comprehension check
#
# Same source, same honesty rule as the recap: questions are written from text
# somebody supplied, never from a video nobody read. A question invented about
# unseen content is worse than no question — the learner gets it "wrong" for
# an answer the lesson never gave.
# ---------------------------------------------------------------------------
#: Kept small on purpose. This is a check that the lesson landed, not an exam;
#: five questions after every lesson is a tax on finishing one.
CHECK_QUESTION_COUNT = 3

CHECK_PROMPT = """\
Write {count} multiple-choice questions checking whether a learner understood \
this lesson.

Work only from the LESSON TEXT below. Every question and every option must be \
answerable from that text alone — do not use outside knowledge, and do not ask \
about anything the text does not cover.

Rules:
- Four options each, exactly one correct.
- Wrong options must be plausible and about the same topic, not obviously \
silly. A learner who did not understand should be able to pick one.
- Ask about what the lesson explains, not about trivia like word order.
- `why` explains in one sentence why the correct option is correct, using the \
lesson's own reasoning.

Write the questions, the options and the explanations in {language}.

Return JSON only, no prose around it, in exactly this shape:
{{"questions": [{{"question": "...", "options": ["...", "...", "...", "..."], \
"answer": 0, "why": "..."}}]}}

LESSON: {title}

LESSON TEXT:
{body}
"""


def build_check(lesson, *, language: str = "Russian") -> list[dict]:
    """Generate the questions. Returns [] when the model gives nothing usable."""
    import json

    from apps.ai.backends import get_chat_backend

    allowed, reason = can_recap(lesson)
    if not allowed:
        raise ValueError(reason)

    backend = get_chat_backend()
    if backend is None:
        raise RuntimeError("No model is configured.")

    raw = backend.reply(
        question=CHECK_PROMPT.format(
            count=CHECK_QUESTION_COUNT,
            language=language,
            title=lesson.title,
            body=recap_source(lesson)[:24_000],
        ),
        facts={},
        history=[],
    )

    return _parse_questions(raw)


def _parse_questions(raw: str) -> list[dict]:
    """Read the model's JSON, and keep only questions that are actually usable.

    Models wrap JSON in prose or fences often enough that finding the object is
    part of the job. Beyond that, every question is checked structurally: a
    malformed one would render as an unanswerable widget, and dropping it is
    better than showing it.
    """
    import json
    import re

    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    try:
        payload = json.loads(text)
    except ValueError:
        logger.warning("Check questions were not valid JSON.")
        return []

    questions = []
    for item in payload.get("questions", []):
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("question", "")).strip()
        options = item.get("options")
        answer = item.get("answer")
        if not prompt or not isinstance(options, list) or len(options) < 2:
            continue
        options = [str(option).strip() for option in options if str(option).strip()]
        if len(options) < 2:
            continue
        if not isinstance(answer, int) or not 0 <= answer < len(options):
            continue
        questions.append(
            _shuffle_options(
                {
                    "question": prompt,
                    "options": options,
                    "answer": answer,
                    "why": str(item.get("why", "")).strip(),
                }
            )
        )
    return questions[:CHECK_QUESTION_COUNT]


def _shuffle_options(question: dict) -> dict:
    """Move the correct answer somewhere unpredictable.

    Models write the right option first far more often than chance — in the
    first live run all three answers were option (a), so answering "a" to
    everything scored 100%. A check that rewards a fixed guess measures
    nothing, and no amount of prompting reliably fixes the habit. Shuffling
    once, here, does.
    """
    import random

    correct = question["options"][question["answer"]]
    options = list(question["options"])
    random.shuffle(options)
    return {**question, "options": options, "answer": options.index(correct)}


def grade(questions: list[dict], answers: dict) -> tuple[int, list[dict]]:
    """Mark the attempt. Returns the percentage and per-question feedback.

    Grading happens here, never in the browser: the answer key is the one thing
    a quiz must not ship to the person taking it.
    """
    results = []
    correct = 0
    for index, question in enumerate(questions):
        chosen = answers.get(str(index), answers.get(index))
        chosen = chosen if isinstance(chosen, int) else None
        is_right = chosen == question["answer"]
        correct += 1 if is_right else 0
        results.append(
            {
                "index": index,
                "chosen": chosen,
                "correct_option": question["answer"],
                "is_correct": is_right,
                "why": question.get("why", ""),
            }
        )
    score = round(100 * correct / len(questions)) if questions else 0
    return score, results
