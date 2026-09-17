"""A comprehension check written for one learner rather than for the cohort.

The parts worth testing here are the ones that hold when the model is at its
worst. A generator that produces good questions on a good day is not the
feature; the feature is that a hallucinated question never reaches a learner,
that two learners get different papers, and that each is graded against the
paper they were actually shown.

The model itself is stubbed throughout. These are tests of the machinery
around it — verification, seeding, grading — and a test that made a real API
call would be slow, expensive, and green or red for reasons that have nothing
to do with the code under it.
"""

import pytest

from apps.common.enums import ModerationStatus
from apps.learning import quizgen

pytestmark = pytest.mark.django_db

#: Over MIN_SOURCE_CHARS on purpose. `can_recap` refuses a lesson too short to
#: summarise, and a fixture under that line tests the guard rather than the
#: generator — which is how the first version of this file failed.
LESSON_BODY = (
    "A confirmed skill is one the platform has evidence for. Evidence comes "
    "from a test result, a course completion, or an employer's verification. "
    "A self-declared skill is not confirmed, and matching weights it lower. "
    "The confidence figure says how much the platform trusts the level. "
    "Evidence has a weight, and the weights are not equal. An employer's "
    "verification outranks a test result, because somebody staked their own "
    "judgement on it. A test result outranks a course completion for a hard "
    "skill and the reverse holds for a soft one, because a written test cannot "
    "observe how a person works with other people. "
    "Nothing a learner types about themselves ever becomes confirmed on its "
    "own. That is the rule the whole model rests on: matching multiplies a "
    "skill's level by the confidence in it, so a wall of self-declared skills "
    "cannot outrank a single tested one, and confident self-reporting buys "
    "nothing."
)


@pytest.fixture
def lesson(db, employer):
    from apps.learning.models import Course, CourseModule, Lesson

    course = Course.objects.create(
        title="Evidence", slug="evidence", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )
    module = CourseModule.objects.create(course=course, title="Basics", order=0)
    return Lesson.objects.create(
        module=module, title="What counts as evidence", order=0,
        content=LESSON_BODY, is_free_preview=True,
    )


def question(answer=0, quote=None, prefix="Q"):
    return {
        "question": f"{prefix}: what is a confirmed skill?",
        "options": ["A", "B", "C", "D"],
        "answer": answer,
        "why": "Because the lesson says so.",
        "quote": quote or "A confirmed skill is one the platform has evidence for.",
    }


# -- verification: the lesson has to support the question ------------------
def test_a_question_quoting_the_lesson_survives():
    kept = quizgen.verify([question()], LESSON_BODY)
    assert len(kept) == 1


def test_a_question_quoting_something_the_lesson_never_said_is_discarded():
    """The expensive failure: a learner marked wrong for an answer the lesson
    never gave."""
    invented = question(quote="Confirmed skills expire after twelve months.")
    assert quizgen.verify([invented], LESSON_BODY) == []


def test_a_quote_too_short_to_mean_anything_is_discarded():
    """Three words match by accident; they are not evidence of grounding."""
    assert quizgen.verify([question(quote="evidence")], LESSON_BODY) == []


def test_whitespace_in_the_quote_does_not_break_the_match():
    spaced = question(
        quote="A confirmed skill   is one\nthe platform has evidence for."
    )
    assert len(quizgen.verify([spaced], LESSON_BODY)) == 1


@pytest.mark.parametrize(
    "broken",
    [
        {"options": ["A", "B", "C"]},          # three options
        {"options": ["A", "A", "B", "C"]},     # a duplicate
        {"answer": 7},                         # out of range
        {"answer": "0"},                       # a string index grades everyone wrong
        {"question": "  "},                    # nothing asked
    ],
)
def test_a_malformed_question_is_discarded(broken):
    assert quizgen.verify([{**question(), **broken}], LESSON_BODY) == []


# -- variation: two learners must not get the same paper -------------------
def test_two_learners_are_asked_from_different_angles():
    mine = quizgen.facets_for("user-a:lesson-1:0")
    yours = quizgen.facets_for("user-b:lesson-1:0")
    assert mine != yours


def test_the_angles_within_one_paper_never_repeat():
    """Sampling three of six draws a duplicate about half the time, and two
    questions from the same angle tend to be the same question."""
    for seed in ("a:b:0", "c:d:0", "e:f:1", "g:h:2"):
        facets = quizgen.facets_for(seed)
        assert len(set(facets)) == len(facets) == quizgen.QUESTION_COUNT


def test_a_regenerated_paper_differs_from_the_first():
    assert quizgen.facets_for("u:l:0") != quizgen.facets_for("u:l:1")


# -- shuffling: the answer must travel with its option ---------------------
def test_shuffling_keeps_the_answer_pointing_at_the_correct_option():
    original = {
        "question": "Which?",
        "options": ["right", "wrong 1", "wrong 2", "wrong 3"],
        "answer": 0,
        "why": "-",
    }

    for seed in ("a:b:0", "c:d:0", "e:f:0", "g:h:0", "i:j:0"):
        shuffled = quizgen.shuffle_options([original], seed)[0]
        assert shuffled["options"][shuffled["answer"]] == "right"
        assert sorted(shuffled["options"]) == sorted(original["options"])


def test_shuffling_is_stable_for_one_learner():
    """Reloading the page must not move the options under somebody who is
    halfway through answering."""
    original = [{"question": "?", "options": ["a", "b", "c", "d"], "answer": 1, "why": "-"}]

    first = quizgen.shuffle_options(original, "user-1:lesson-1:0")
    again = quizgen.shuffle_options(original, "user-1:lesson-1:0")
    assert first == again


def test_shuffling_differs_between_learners():
    """Otherwise 'it's the third one' still travels."""
    original = [
        {"question": "?", "options": ["a", "b", "c", "d"], "answer": 0, "why": "-"}
        for _ in range(6)
    ]

    mine = quizgen.shuffle_options(original, "user-1:lesson-1:0")
    yours = quizgen.shuffle_options(original, "user-2:lesson-1:0")
    assert [q["options"] for q in mine] != [q["options"] for q in yours]


def test_two_questions_in_one_paper_do_not_share_a_permutation():
    """A single stream would give identical option orders to identical option
    lists, which puts the answer in the same place twice."""
    same = [
        {"question": f"{i}", "options": ["a", "b", "c", "d"], "answer": 0, "why": "-"}
        for i in range(4)
    ]
    orders = [q["options"] for q in quizgen.shuffle_options(same, "u:l:0")]
    assert len(set(map(tuple, orders))) > 1


# -- the generator end to end, with the model stubbed ----------------------
class StubBackend:
    """Returns whatever it was given, and records the prompt it was asked."""

    def __init__(self, payload):
        self.payload = payload
        self.prompt = None
        self.schema = None

    def structured(self, *, prompt, schema, system="", effort="medium"):
        self.prompt = prompt
        self.schema = schema
        return self.payload


def test_the_answer_key_leaves_but_the_quote_does_not(lesson, student, monkeypatch):
    """The quote is a verification device. Shipping it would put the answer in
    the payload the page receives."""
    backend = StubBackend({"questions": [question(prefix="Q1"), question(prefix="Q2")]})
    monkeypatch.setattr("apps.ai.backends.get_chat_backend", lambda: backend)

    built = quizgen.build_for(lesson, student, language="English")

    assert len(built) == 2
    assert all("quote" not in item for item in built)
    assert all("answer" in item for item in built)


def test_a_paper_where_nothing_verifies_comes_back_empty(lesson, student, monkeypatch):
    """The caller falls back to the shared check rather than showing a quiz the
    lesson does not support."""
    invented = question(quote="The platform issues a certificate after each lesson.")
    backend = StubBackend({"questions": [invented]})
    monkeypatch.setattr("apps.ai.backends.get_chat_backend", lambda: backend)

    assert quizgen.build_for(lesson, student, language="English") == []


def test_the_generator_is_told_to_use_the_lesson_and_nothing_else(lesson, student, monkeypatch):
    backend = StubBackend({"questions": [question()]})
    monkeypatch.setattr("apps.ai.backends.get_chat_backend", lambda: backend)

    quizgen.build_for(lesson, student, language="English")

    assert "Work only from the LESSON TEXT" in backend.prompt
    assert LESSON_BODY in backend.prompt
    # The schema is what stops a string answer index reaching the grader.
    assert backend.schema["properties"]["questions"]["items"]["properties"]["answer"][
        "type"
    ] == "integer"


def test_no_model_configured_raises_rather_than_inventing(lesson, student, monkeypatch):
    monkeypatch.setattr("apps.ai.backends.get_chat_backend", lambda: None)

    with pytest.raises(RuntimeError):
        quizgen.build_for(lesson, student, language="English")


# -- through the endpoints -------------------------------------------------
#
# The generator being correct is not the same as the feature being correct.
# What matters at this level: each learner is served their own paper, the
# answer key never appears in the response, and the grading uses the paper
# that learner was actually shown -- grading a personal paper against the
# shared key marks correct answers wrong, and the shuffled option order alone
# guarantees it.
def enrol(user, lesson):
    from apps.learning.models import Enrollment

    return Enrollment.objects.create(user=user, course=lesson.module.course)


def paper(index_offset=0):
    """A paper whose correct option is identifiable after shuffling."""
    return {
        "questions": [
            {
                "question": f"Question {i}?",
                "options": [f"right-{i}", f"wrong-{i}-a", f"wrong-{i}-b", f"wrong-{i}-c"],
                "answer": 0,
                "why": "The lesson says so.",
                "quote": "Nothing a learner types about themselves ever becomes "
                         "confirmed on its own.",
            }
            for i in range(index_offset, index_offset + 3)
        ]
    }


def test_each_learner_is_served_their_own_paper(
    auth, student, other_student, lesson, monkeypatch
):
    monkeypatch.setattr(
        "apps.ai.backends.get_chat_backend", lambda: StubBackend(paper())
    )
    enrol(student, lesson)
    enrol(other_student, lesson)

    url = f"/api/v1/learning/lessons/{lesson.id}/check/"
    mine = auth(student).post(url)
    yours = auth(other_student).post(url)

    assert mine.status_code == 200, mine.data
    assert mine.data["personal"] is True
    assert yours.data["personal"] is True

    # Same source questions from the stub, so if the option order still
    # matches, the per-learner shuffle is not running.
    mine_options = [q["options"] for q in mine.data["questions"]]
    yours_options = [q["options"] for q in yours.data["questions"]]
    assert mine_options != yours_options


def test_the_answer_key_never_reaches_the_page(auth, student, lesson, monkeypatch):
    monkeypatch.setattr(
        "apps.ai.backends.get_chat_backend", lambda: StubBackend(paper())
    )
    enrol(student, lesson)

    response = auth(student).post(f"/api/v1/learning/lessons/{lesson.id}/check/")

    for question_row in response.data["questions"]:
        assert "answer" not in question_row
        assert "why" not in question_row
        assert "quote" not in question_row


def test_the_same_learner_gets_the_same_paper_back(auth, student, lesson, monkeypatch):
    """Reloading is not a reroll: it would cost a model call per refresh and
    move the questions under somebody mid-answer."""
    monkeypatch.setattr(
        "apps.ai.backends.get_chat_backend", lambda: StubBackend(paper())
    )
    enrol(student, lesson)
    url = f"/api/v1/learning/lessons/{lesson.id}/check/"

    first = auth(student).post(url).data["questions"]
    again = auth(student).post(url).data["questions"]

    assert first == again


def test_the_attempt_is_graded_against_that_learners_own_paper(
    auth, student, lesson, monkeypatch
):
    """The bug this guards: grading a shuffled personal paper against the
    shared key, which marks a correct answer wrong."""
    from apps.learning.models import LessonQuiz

    monkeypatch.setattr(
        "apps.ai.backends.get_chat_backend", lambda: StubBackend(paper())
    )
    enrol(student, lesson)
    client = auth(student)

    served = client.post(f"/api/v1/learning/lessons/{lesson.id}/check/").data["questions"]

    # Answer correctly by finding the right option in what was actually served.
    answers = {
        str(i): q["options"].index(f"right-{i}") for i, q in enumerate(served)
    }

    response = client.post(
        f"/api/v1/learning/lessons/{lesson.id}/check/submit/",
        {"answers": answers},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["score"] == 100

    # And the stored key really is shuffled, so the assertion above was not
    # passing by accident on an unshuffled paper.
    stored = LessonQuiz.objects.get(lesson=lesson, user=student)
    assert any(q["answer"] != 0 for q in stored.questions)


def test_without_a_model_everyone_falls_back_to_the_shared_check(
    auth, student, lesson, monkeypatch
):
    """The feature degrades to what it was rather than disappearing."""
    monkeypatch.setattr("apps.ai.backends.get_chat_backend", lambda: None)
    enrol(student, lesson)

    response = auth(student).post(f"/api/v1/learning/lessons/{lesson.id}/check/")

    assert response.status_code == 200, response.data
    assert response.data["available"] is False
    assert response.data["reason"] == "no_model"


def test_an_edited_lesson_invalidates_the_paper(auth, student, lesson, monkeypatch):
    """Otherwise a learner is graded on a paragraph the author deleted."""
    monkeypatch.setattr(
        "apps.ai.backends.get_chat_backend", lambda: StubBackend(paper())
    )
    enrol(student, lesson)
    client = auth(student)
    client.post(f"/api/v1/learning/lessons/{lesson.id}/check/")

    lesson.content = lesson.content + " The author added a paragraph here."
    lesson.save(update_fields=["content"])

    response = client.post(
        f"/api/v1/learning/lessons/{lesson.id}/check/submit/",
        {"answers": {"0": 0}},
        format="json",
    )

    assert response.status_code == 409, response.data
    assert response.data["stale"] is True
