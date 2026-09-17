"""A comprehension check written for one learner, not for the cohort.

The lesson already had a check: three questions generated once from the
lesson's own text, cached on the lesson, and served to everybody. That is a
fine way to ask "did this land" and a useless way to ask it twice, because the
first person through posts the answers and the check stops measuring anything.

So a quiz is generated per learner. The variation is real rather than
cosmetic:

  * different questions, because the generator is told which facet of the
    lesson to build each one from and the facets are rotated per learner;
  * different option order, shuffled from a seed derived from the learner and
    the attempt, so two people comparing "the answer is B" learn nothing;
  * different distractors, because they are written alongside their question.

Three things are deliberately NOT claimed:

  1. **This is not proctoring.** Two people sitting together can still work
     through a quiz together. What it stops is the cheaper and far more common
     failure: an answer key circulating in a group chat.
  2. **The model can be wrong.** Every generated set is checked against the
     lesson text before it is served, and anything that fails is discarded
     rather than shown. What survives is still a machine's reading of a
     lesson, which is why a learner's result here feeds `LessonProgress` and
     not their skill evidence -- the platform's confirmed-skill bar is a test
     an author wrote.
  3. **A quiz is not free.** Each one is a model call, so it is generated once
     per learner per lesson and stored; asking again returns the same one
     until the lesson changes underneath it.
"""

from __future__ import annotations

import hashlib
import logging
import random

from .recap import can_recap, recap_source

logger = logging.getLogger(__name__)

#: Kept small on purpose. This is a check that the lesson landed, not an exam;
#: five questions after every lesson is a tax on finishing one.
QUESTION_COUNT = 3

#: The angles a question can be asked from.
#:
#: Rotated per learner so two people do not get three questions about the same
#: paragraph. They are phrased as instructions to the generator rather than as
#: labels, because "ask about a consequence" and "ask a definition" produce
#: genuinely different questions from the same text, where "vary the questions"
#: produces the same three with the words moved around.
FACETS = [
    "a definition or a term the lesson introduces",
    "a consequence: what follows from something the lesson states",
    "a comparison between two things the lesson describes",
    "the order or the steps of a process the lesson sets out",
    "a concrete situation the lesson's rule would apply to",
    "a common mistake the lesson warns against, or corrects",
]

QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "answer": {"type": "integer", "minimum": 0, "maximum": 3},
                    "why": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["question", "options", "answer", "why", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

PROMPT = """\
Write {count} multiple-choice questions checking whether a learner understood \
this lesson.

Work only from the LESSON TEXT below. Every question and every option must be \
answerable from that text alone — do not use outside knowledge, and do not ask \
about anything the text does not cover.

Ask each question from a different angle. Use these, in order, one per \
question:
{facets}

Rules:
- Four options each, exactly one correct.
- Wrong options must be plausible and about the same topic, not obviously \
silly. A learner who did not understand should be able to pick one.
- Ask about what the lesson explains, not about trivia like word order.
- `why` explains in one sentence why the correct option is correct, using the \
lesson's own reasoning.
- `quote` is the exact sentence from LESSON TEXT that makes the correct option \
correct. Copy it verbatim — it is checked against the text, and a question \
whose quote is not in the lesson is discarded.

Write the questions, the options and the explanations in {language}.

LESSON: {title}

LESSON TEXT:
{body}
"""


def facets_for(seed: str) -> list[str]:
    """Which angles this learner's questions come from.

    Rotated rather than sampled: a rotation guarantees three *different*
    angles, where sampling three from six draws a duplicate about half the
    time — and two questions from the same angle on the same lesson tend to be
    the same question.
    """
    offset = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(FACETS)
    return [FACETS[(offset + i) % len(FACETS)] for i in range(QUESTION_COUNT)]


def _normalise(text: str) -> str:
    """Whitespace-flattened and lowercased, for comparing a quote to a body."""
    return " ".join(text.lower().split())


def verify(questions: list[dict], body: str) -> list[dict]:
    """Keep the questions the lesson actually supports.

    The model is asked to quote the sentence that makes its answer correct,
    and the quote is checked against the lesson here. This is a cheap test and
    it catches the expensive failure: a question invented about content the
    lesson does not contain, which a learner then gets 'wrong' for an answer
    the lesson never gave.

    It does not catch a question that is merely a bad question. Nothing here
    does, which is why the result feeds progress rather than skill evidence.
    """
    haystack = _normalise(body)
    kept = []

    for item in questions:
        options = item.get("options") or []
        answer = item.get("answer")
        quote = (item.get("quote") or "").strip()

        if len(options) != 4 or len(set(options)) != 4:
            continue
        if not isinstance(answer, int) or not 0 <= answer < 4:
            continue
        if not item.get("question", "").strip():
            continue
        # A very short quote matches by accident; an absent one is a question
        # about something the lesson does not say.
        if len(quote) < 20 or _normalise(quote) not in haystack:
            continue
        kept.append(item)

    return kept


def shuffle_options(questions: list[dict], seed: str) -> list[dict]:
    """Reorder each question's options, keeping `answer` pointing at the right one.

    Seeded from the learner and the lesson, so it is stable — reloading the
    page must not move the options under somebody midway through answering —
    and different between learners, so "it's the third one" travels badly.

    `random.Random(seed)` rather than the module-level generator: this must not
    depend on, or disturb, global random state that something else is using.
    """
    rng = random.Random(seed)
    out = []

    for index, item in enumerate(questions):
        options = list(item["options"])
        correct = options[item["answer"]]
        # Per-question stream, so two questions with similar options do not
        # get the same permutation.
        random.Random(f"{seed}:{index}").shuffle(options)
        out.append({**item, "options": options, "answer": options.index(correct)})

    del rng
    return out


def build_for(lesson, user, *, language: str = "Russian", attempt: int = 0) -> list[dict]:
    """Generate this learner's questions for this lesson.

    Returns [] when nothing survived verification — the caller falls back to
    the lesson's shared check rather than showing a learner a quiz the lesson
    does not support.
    """
    from apps.ai.backends import get_chat_backend

    allowed, reason = can_recap(lesson)
    if not allowed:
        raise ValueError(reason)

    backend = get_chat_backend()
    if backend is None:
        raise RuntimeError("No model is configured.")

    body = recap_source(lesson)[:24_000]
    seed = f"{user.id}:{lesson.id}:{attempt}"
    facets = facets_for(seed)

    data = backend.structured(
        prompt=PROMPT.format(
            count=QUESTION_COUNT,
            facets="\n".join(f"{i + 1}. {f}" for i, f in enumerate(facets)),
            language=language,
            title=lesson.title,
            body=body,
        ),
        schema=QUIZ_SCHEMA,
        system=(
            "You write comprehension checks from a lesson's own text. You never "
            "ask about anything the text does not contain."
        ),
        effort="medium",
    )

    questions = verify(data.get("questions") or [], body)
    if not questions:
        logger.warning(
            "Generated quiz for lesson %s discarded: nothing passed verification.",
            lesson.id,
        )
        return []

    # The quote did its job at verification time and is not part of the quiz.
    # Leaving it in would put the answer in the payload the page receives.
    questions = [
        {k: v for k, v in item.items() if k != "quote"} for item in questions
    ]
    return shuffle_options(questions, seed)
