"""Answering a learner's question about the lesson in front of them.

THE RULE THIS MODULE EXISTS TO ENFORCE: the answer comes from the course, or
there is no answer. A general model asked "why is this loop O(n log n)?" will
produce a confident, fluent paragraph whether or not the lesson ever said so —
and a learner who is told something the course did not teach, in the course's
own voice, has been misled by the platform rather than by a chatbot. So every
reply is built from excerpts of this lesson's own text, the excerpts are
returned alongside it, and when nothing in the lesson covers the question the
answer says exactly that.

WHAT THE PLATFORM CAN ACTUALLY READ, stated plainly because it decides what
this feature can promise:

  - the lesson body, written by the author;
  - the transcript, when the author pasted one (YouTube hands them one);
  - the title and description of each attached material.

WHAT IT CANNOT READ, today:

  - the video. The platform holds a link, not audio. Without a transcript
    there is no record of what was said, and "answer questions about the
    moment they are watching" is only as good as the transcript behind it.
    `apps/learning/recap.py` made the same call for the same reason.
  - the inside of an uploaded PDF, EPUB or image. Those are bytes on disk;
    turning them into text needs a parser this project does not have yet.

`readiness()` reports both of those per lesson, so an author can see what the
assistant will and will not be able to answer *before* they publish, rather
than discovering it from a learner's complaint.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: Below this there is not enough written down to answer anything from.
MIN_SOURCE_CHARS = 200

#: How much of the lesson may be handed to the model in one request. Long
#: transcripts run to tens of thousands of characters and most of any one is
#: irrelevant to any one question; the selection below is what keeps the call
#: affordable and the answer on topic.
MAX_CONTEXT_CHARS = 6000

#: A transcript is cut into pieces this size before ranking, so a question
#: about one minute of a lecture does not drag in the whole hour.
CHUNK_CHARS = 700

QUESTION_MAX_CHARS = 500

#: The tutor's rules — instructions, and so the only text sent in `system`.
#:
#: The lesson title, the excerpts and the question used to be formatted into
#: this same string and sent together as the user's message, which put a
#: sentence an author planted in a transcript on exactly the same footing as
#: the rules below. They now travel as separate, fenced data blocks; see
#: apps/ai/prompting.py. Nothing in this string comes from a request.
TUTOR_RULES = """\
You are helping a learner who is part-way through a lesson and has stopped to \
ask about something they did not follow.

Answer ONLY from the lesson material you are given. It is the entire course \
material available to you.

- If the material answers the question, answer it plainly, in two to five \
sentences, using the same terms the lesson uses.
- If the learner asks how something is used in practice, answer only with \
examples the material itself gives.
- If the material does not contain the answer, say so in one sentence and \
name what the lesson does cover instead. Do not fill the gap from your own \
knowledge, do not guess, and do not invent examples, figures, commands or \
links. The learner will read this as what their course taught them.

Write in {language}. No preamble, no sign-off.\
"""

#: What the learner reads when a question is declined — asked for the
#: instructions, or answered with something that could not be passed on.
#: Keyed by the language name the view hands in.
DECLINED = {
    "Russian": (
        "Я могу отвечать только на вопросы по материалу этого урока. "
        "Спросите о том, что в нём непонятно."
    ),
    "Uzbek (latin script)": (
        "Men faqat shu dars materiali bo'yicha savollarga javob bera olaman. "
        "Darsda nima tushunarsiz bo'lsa, shuni so'rang."
    ),
    "English": (
        "I can only answer questions about this lesson's material. "
        "Ask about the part of it that was unclear."
    ),
}


class Source:
    """One piece of readable course text, with a label the learner will see."""

    __slots__ = ("kind", "label", "text")

    def __init__(self, kind: str, label: str, text: str):
        self.kind = kind
        self.label = label
        self.text = text.strip()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Source {self.kind} {self.label!r} {len(self.text)} chars>"


def lesson_sources(lesson) -> list[Source]:
    """Everything about this lesson that is text, in order of authority.

    The transcript comes first: it is a record of what was actually said in
    the video the learner is watching, where the body is often a heading and
    three bullet points.
    """
    sources: list[Source] = []

    transcript = (lesson.transcript or "").strip()
    if transcript:
        sources.append(Source("transcript", "transcript", transcript))

    body = (lesson.content or "").strip()
    if body:
        sources.append(Source("body", "lesson", body))

    #: A material contributes only what the author typed about it. Its bytes
    #: are not readable here — see the module docstring — so the text is the
    #: description, never a pretend summary of a file nobody parsed.
    #:
    #: Both scopes, on purpose: the worksheet filed under this lesson and the
    #: reading list filed under the whole course are the same thing to the
    #: learner asking about them, and the course-level ones are exactly the
    #: books and handbooks an employer uploads once for the entire syllabus.
    from django.db.models import Q

    from .models import CourseMaterial

    materials = CourseMaterial.objects.filter(
        Q(lesson=lesson) | Q(course_id=lesson.module.course_id, lesson__isnull=True)
    ).order_by("lesson_id", "created_at")

    for material in materials:
        described = (material.description or "").strip()
        if described:
            sources.append(
                Source("material", material.title or "material", described)
            )

    return sources


def can_answer(lesson) -> tuple[bool, str]:
    """Whether there is enough written down to ground an answer.

    Mirrors `recap.can_recap`: the refusal is the feature. A lesson that is a
    video link and nothing else has no text, and a model given no text writes
    from its own memory of the topic, which is precisely the thing this must
    not do.
    """
    total = sum(len(source.text) for source in lesson_sources(lesson))
    if total < MIN_SOURCE_CHARS:
        return False, "no_source_text"
    return True, ""


def readiness(lesson) -> dict:
    """What the assistant will be able to answer on this lesson, and what not.

    For the author, before publishing. Every field is a fact about this
    lesson, not an estimate: whether a transcript exists, how much text there
    is to work from, and how many attached materials the assistant can only
    see the title of.
    """
    sources = lesson_sources(lesson)
    chars = sum(len(source.text) for source in sources)
    materials = list(lesson.materials.all())
    opaque = [
        material.title or str(material.id)
        for material in materials
        if not (material.description or "").strip()
    ]

    return {
        "can_answer": chars >= MIN_SOURCE_CHARS,
        "characters": chars,
        "has_transcript": bool((lesson.transcript or "").strip()),
        "has_video": bool((lesson.video_url or "").strip()),
        #: A video with no transcript is the gap worth naming: the assistant
        #: cannot answer "what did he say at 4:20", because nothing recorded it.
        "video_unreadable": bool((lesson.video_url or "").strip())
        and not bool((lesson.transcript or "").strip()),
        "materials_total": len(materials),
        "materials_without_text": opaque,
    }


_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    """Words worth matching on: three letters or more, lowercased.

    Deliberately crude. There is no vector store here and adding one for a
    lesson-sized haystack would be machinery without a purpose — a lesson is
    a few thousand words, and word overlap picks the right paragraph out of
    ten as reliably as anything heavier would.
    """
    return {word.lower() for word in _WORD.findall(text) if len(word) >= 3}


def _chunks(source: Source) -> list[tuple[Source, str]]:
    """Split a long source on paragraph breaks, packing up to CHUNK_CHARS."""
    if len(source.text) <= CHUNK_CHARS:
        return [(source, source.text)]

    pieces: list[tuple[Source, str]] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", source.text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > CHUNK_CHARS:
            pieces.append((source, current))
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph

        # A single paragraph longer than the budget is cut on length; a
        # transcript pasted as one wall of text is the common case.
        while len(current) > CHUNK_CHARS:
            pieces.append((source, current[:CHUNK_CHARS]))
            current = current[CHUNK_CHARS:]

    if current:
        pieces.append((source, current))
    return pieces


def select_excerpts(
    lesson, question: str, *, budget: int = MAX_CONTEXT_CHARS
) -> list[tuple[Source, str]]:
    """The parts of the lesson most likely to contain the answer.

    Ranked by how many of the question's words appear in the chunk. Ties, and
    the case where the question shares no words with anything — a learner
    asking "I didn't get the last bit" — fall back to document order, so the
    model still receives the lesson rather than nothing.
    """
    wanted = _tokens(question)
    pool: list[tuple[Source, str]] = []
    for source in lesson_sources(lesson):
        pool.extend(_chunks(source))

    scored = sorted(
        enumerate(pool),
        key=lambda item: (-len(wanted & _tokens(item[1][1])), item[0]),
    )

    chosen: list[tuple[int, tuple[Source, str]]] = []
    used = 0
    for index, chunk in scored:
        if used + len(chunk[1]) > budget and chosen:
            continue
        chosen.append((index, chunk))
        used += len(chunk[1])
        if used >= budget:
            break

    # Hand them to the model in the order they appear in the lesson: excerpts
    # shuffled by relevance read as a jumble and invite the model to invent
    # connective tissue between them.
    return [chunk for _, chunk in sorted(chosen, key=lambda item: item[0])]


def _declined(language: str) -> dict:
    """A plain answer that says what the tutor is for — not an error.

    `declined` lets a caller tell this apart from a grounded answer without
    changing what the learner sees.
    """
    return {
        "available": True,
        "answer": DECLINED.get(language, DECLINED["English"]),
        "grounded_on": [],
        "declined": True,
    }


def build_answer(lesson, question: str, *, language: str, user=None) -> dict:
    """Ask the model, grounded, and report what it was grounded on."""
    from apps.ai import prompting
    from apps.ai.backends import get_chat_backend
    from apps.ai.models import SafetyAction, SafetySeverity
    from apps.ai.safety import record_event

    excerpts = select_excerpts(lesson, question)
    if not excerpts:
        return {"available": False, "reason": "no_source_text", "answer": ""}

    backend = get_chat_backend()
    if backend is None or not backend.is_ready():
        return {"available": False, "reason": "no_provider", "answer": ""}

    # Asking for the instructions outright is answered here, without a model.
    if prompting.looks_like_extraction_attempt(question):
        record_event(
            rule="prompt_extraction", action=SafetyAction.BLOCKED,
            severity=SafetySeverity.LOW, user=user,
        )
        return _declined(language)

    # The title is the author's words too, so it is material, not instruction.
    material = [("lesson title", lesson.title or "")]
    material += [(source.label, text) for source, text in excerpts]

    try:
        answer = backend.run_task(
            instructions=TUTOR_RULES.format(language=language),
            material=material,
            question=question,
            user=user,
        )
    except prompting.OutputRejected:
        # The reply recited its instructions or carried a link the lesson never
        # gave. Recorded on the safety queue by the backend; not shown.
        return _declined(language)
    except Exception:  # pragma: no cover - provider failures are not the caller's problem
        logger.exception("Lesson tutor call failed for lesson %s", lesson.id)
        return {"available": False, "reason": "provider_failed", "answer": ""}

    #: The labels, de-duplicated in first-seen order — the learner is told
    #: which parts of the course the answer came out of, which is the only way
    #: to check it.
    labels: list[str] = []
    for source, _ in excerpts:
        if source.label not in labels:
            labels.append(source.label)

    return {
        "available": True,
        "answer": (answer or "").strip(),
        "grounded_on": labels,
    }
