"""The lesson tutor answers from the lesson, or it does not answer.

Three things are worth a test here, and they are the three ways this feature
could quietly become harmful:

  1. It must not open to somebody who is not in the course. The endpoint hands
     back excerpts of the course text, so an unguarded `ask` is a way to read
     paid material one question at a time.
  2. It must refuse when there is nothing written down. A lesson that is a
     video link and a title has no text; a model handed that answers from its
     own memory of the subject, in the platform's voice, and the learner
     cannot tell the difference.
  3. It must send the *relevant* part of a long transcript. The selection is
     the difference between an answer about the right minute of a lecture and
     an answer about whatever happened to be at the top of the file.
"""

import pytest

from apps.common.enums import ModerationStatus

pytestmark = pytest.mark.django_db

LESSONS_URL = "/api/v1/learning/lessons/"


@pytest.fixture
def lesson(db, employer):
    from apps.learning.models import Course, CourseModule, Lesson

    course = Course.objects.create(
        title="Data Analysis",
        slug="data-analysis",
        author=employer,
        employer=employer.employer_profile,
        status=ModerationStatus.PUBLISHED,
    )
    module = CourseModule.objects.create(course=course, title="Joins", order=0)
    return Lesson.objects.create(
        module=module,
        title="SQL joins",
        content=(
            "An INNER JOIN keeps only the rows that match on both sides. "
            "A LEFT JOIN keeps every row of the left table and fills the "
            "missing right-hand columns with NULL. " * 4
        ),
        order=0,
        is_free_preview=True,
    )


@pytest.fixture
def bare_lesson(db, employer):
    """A video and a title. Nothing anybody wrote down."""
    from apps.learning.models import Course, CourseModule, Lesson

    course = Course.objects.create(
        title="Statistics",
        slug="statistics",
        author=employer,
        employer=employer.employer_profile,
        status=ModerationStatus.PUBLISHED,
    )
    module = CourseModule.objects.create(course=course, title="Basics", order=0)
    return Lesson.objects.create(
        module=module,
        title="Variance",
        video_url="https://www.youtube.com/watch?v=8bq4t8qzQIk",
        content="",
        order=0,
        is_free_preview=True,
    )


def test_a_stranger_cannot_ask_about_a_gated_lesson(auth, student, lesson):
    """The reply quotes the course, so the gate is the same as the body's."""
    lesson.is_free_preview = False
    lesson.save(update_fields=["is_free_preview"])

    response = auth(student).post(
        f"{LESSONS_URL}{lesson.id}/ask/", {"question": "What is a LEFT JOIN?"},
        format="json",
    )

    assert response.status_code in {403, 404}, response.data


def test_a_lesson_with_no_text_refuses_instead_of_inventing(
    auth, student, bare_lesson
):
    """The refusal is the feature — see apps/learning/tutor.py."""
    response = auth(student).post(
        f"{LESSONS_URL}{bare_lesson.id}/ask/",
        {"question": "What did he say about variance?"},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["available"] is False
    assert response.data["reason"] == "no_source_text"
    assert response.data["answer"] == ""


def test_an_empty_question_is_rejected(auth, student, lesson):
    response = auth(student).post(
        f"{LESSONS_URL}{lesson.id}/ask/", {"question": "   "}, format="json"
    )
    assert response.status_code == 400, response.data


def test_a_very_long_question_is_rejected(auth, student, lesson):
    from apps.learning.tutor import QUESTION_MAX_CHARS

    response = auth(student).post(
        f"{LESSONS_URL}{lesson.id}/ask/",
        {"question": "why " * QUESTION_MAX_CHARS},
        format="json",
    )
    assert response.status_code == 400, response.data


# -- the selection ---------------------------------------------------------
def test_the_excerpt_comes_from_the_part_that_answers_the_question(db, lesson):
    """A long transcript, and a question about one paragraph of it.

    Without selection the model would be handed the top of the file; the point
    of this is that it is handed the minute the learner is actually asking
    about.
    """
    from apps.learning.tutor import select_excerpts

    filler = "Nothing to do with the question at hand. " * 40
    lesson.transcript = (
        f"{filler}\n\n"
        "A cross join produces every combination of rows on both sides, "
        "which is why it is dangerous on large tables.\n\n"
        f"{filler}"
    )
    lesson.save(update_fields=["transcript"])

    excerpts = select_excerpts(lesson, "what does a cross join do?")
    joined = " ".join(text for _, text in excerpts)

    assert "cross join produces every combination" in joined
    assert excerpts[0][0].label == "transcript"


def test_selection_stays_inside_its_budget(db, lesson):
    from apps.learning.tutor import MAX_CONTEXT_CHARS, select_excerpts

    lesson.transcript = "A sentence about joins and tables. " * 2000
    lesson.save(update_fields=["transcript"])

    excerpts = select_excerpts(lesson, "joins")
    total = sum(len(text) for _, text in excerpts)

    assert total <= MAX_CONTEXT_CHARS + 700, total
    assert total > 0


def test_a_question_sharing_no_words_still_gets_the_lesson(db, lesson):
    """"I didn't get the last bit" has no keywords. It must not return nothing."""
    from apps.learning.tutor import select_excerpts

    excerpts = select_excerpts(lesson, "я не понял")

    assert excerpts, "a question with no overlap must still be answerable"


# -- what the author is told before publishing ------------------------------
def test_readiness_names_a_video_nobody_transcribed(db, bare_lesson):
    from apps.learning.tutor import readiness

    report = readiness(bare_lesson)

    assert report["has_video"] is True
    assert report["has_transcript"] is False
    assert report["video_unreadable"] is True
    assert report["can_answer"] is False


def test_readiness_clears_once_a_transcript_is_pasted(db, bare_lesson):
    from apps.learning.tutor import readiness

    bare_lesson.transcript = "Variance measures how far the numbers spread. " * 10
    bare_lesson.save(update_fields=["transcript"])

    report = readiness(bare_lesson)

    assert report["video_unreadable"] is False
    assert report["can_answer"] is True


# -- the call itself -------------------------------------------------------
#
# These two exist because the first version of this feature was broken in a way
# none of the tests above could see. `build_answer` called
# `backend.reply(prompt, history=[])`, but the backend's `reply` is
# keyword-only and requires `facts` — so every question raised TypeError, the
# broad `except Exception` turned it into "provider_failed", and the panel told
# the learner the model was not connected. It was connected. The refusal tests
# all passed, because none of them ever reached a provider.
def test_the_call_matches_the_real_backend_signature():
    """Bind the arguments against the actual backend, without needing a key.

    This is the check that would have caught it, and the one that catches the
    next signature change too.
    """
    import inspect

    from apps.ai.backends import AnthropicChatBackend

    signature = inspect.signature(AnthropicChatBackend.reply)
    # `self` aside, this is exactly what tutor.build_answer passes.
    signature.bind(
        None, question="a prompt", facts={}, history=[]
    )


def test_a_question_reaches_the_model_and_the_sources_come_back(
    db, monkeypatch, lesson
):
    """The success path, end to end, with the provider stubbed out."""
    from apps.ai.backends import AnthropicChatBackend
    from apps.learning import tutor

    seen = {}

    class StubBackend(AnthropicChatBackend):
        def __init__(self):  # no config, no key
            pass

        def is_ready(self):
            return True

        def reply(self, **kwargs):
            seen.update(kwargs)
            return "  An INNER JOIN keeps only matching rows.  "

    monkeypatch.setattr(
        "apps.ai.backends.get_chat_backend", lambda: StubBackend()
    )

    result = tutor.build_answer(lesson, "what is an inner join?", language="English")

    assert result["available"] is True
    assert result["answer"] == "An INNER JOIN keeps only matching rows."
    assert result["grounded_on"] == ["lesson"]
    # The lesson's own words were actually put in front of the model.
    assert "INNER JOIN keeps only the rows" in seen["question"]
    assert "what is an inner join?" in seen["question"]
