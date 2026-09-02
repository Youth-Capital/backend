"""A few questions at the end of a lesson.

The point is to find out whether the lesson landed, so the tests below are
mostly about the two ways a check can be worthless.

**The answer key must not reach the browser.** A quiz that ships its own
answers measures nothing, and the page source is the first place anyone looks.
So the questions go out stripped and the marking happens on the server — and
that is asserted field by field rather than by checking one flag.

**The questions must come from the lesson.** Same rule as the recap: the
platform cannot hear a video, so questions are written from text somebody
supplied. A question invented about unseen content marks a learner wrong for
an answer the lesson never gave, which is worse than asking nothing.
"""

import pytest

from apps.common.enums import ModerationStatus

pytestmark = pytest.mark.django_db

LESSONS_URL = "/api/v1/learning/lessons/"

QUESTIONS = [
    {
        "question": "Что такое отражённый XSS?",
        "options": ["Код из запроса", "Код из базы", "Код в CSS", "Код в заголовке"],
        "answer": 0,
        "why": "Он приходит в параметре запроса и сразу попадает в ответ.",
    },
    {
        "question": "Что делает HttpOnly?",
        "options": ["Шифрует куку", "Прячет её от скриптов", "Сжимает её", "Продлевает её"],
        "answer": 1,
        "why": "Скрипт не может прочитать такую куку, поэтому кража не даёт сессию.",
    },
]


@pytest.fixture
def course(db, employer):
    from apps.learning.models import Course

    return Course.objects.create(
        title="Web Security", slug="web-security", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )


@pytest.fixture
def lesson(db, course):
    from apps.learning.models import CourseModule, Lesson

    module = CourseModule.objects.create(course=course, title="M", order=0)
    return Lesson.objects.create(
        module=module,
        title="XSS",
        order=0,
        is_free_preview=True,
        transcript="Межсайтовый скриптинг подробно разобран здесь. " * 20,
    )


@pytest.fixture
def with_questions(lesson):
    """Questions already generated, so the tests do not call a model."""
    from apps.learning.recap import recap_source, source_fingerprint

    lesson.check_questions = QUESTIONS
    lesson.check_source_hash = source_fingerprint(recap_source(lesson))
    lesson.save(update_fields=["check_questions", "check_source_hash"])
    return lesson


@pytest.fixture
def enrolled(db, student, course):
    from apps.learning.models import Enrollment

    return Enrollment.objects.create(user=student, course=course)


# -- the answer key --------------------------------------------------------
def test_the_questions_arrive_without_their_answers(auth, student, with_questions):
    """The assertion this file exists for."""
    response = auth(student).post(f"{LESSONS_URL}{with_questions.id}/check/")

    assert response.status_code == 200, response.data
    assert response.data["available"] is True
    for row in response.data["questions"]:
        assert "answer" not in row, "the answer key was sent to the learner"
        assert "why" not in row, "the explanation gives the answer away"
        assert row["question"] and row["options"]


def test_the_whole_response_contains_no_answer_key(auth, student, with_questions):
    """Belt and braces: not in a nested field, not under another name."""
    import json

    response = auth(student).post(f"{LESSONS_URL}{with_questions.id}/check/")
    body = json.dumps(response.data, ensure_ascii=False)

    assert '"answer"' not in body
    assert "Он приходит в параметре" not in body, "an explanation leaked"


# -- marking ---------------------------------------------------------------
def test_a_perfect_attempt_scores_100(auth, student, with_questions, enrolled):
    response = auth(student).post(
        f"{LESSONS_URL}{with_questions.id}/check/submit/",
        {"answers": {"0": 0, "1": 1}},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["score"] == 100
    assert all(row["is_correct"] for row in response.data["results"])


def test_a_wrong_answer_comes_back_with_the_reason(
    auth, student, with_questions, enrolled
):
    """A wrong answer with no explanation teaches nothing."""
    response = auth(student).post(
        f"{LESSONS_URL}{with_questions.id}/check/submit/",
        {"answers": {"0": 2, "1": 1}},
        format="json",
    )

    assert response.data["score"] == 50
    first = response.data["results"][0]
    assert first["is_correct"] is False
    assert first["correct_option"] == 0
    assert first["why"], "no explanation for the wrong answer"


def test_an_unanswered_question_is_simply_wrong(auth, student, with_questions, enrolled):
    response = auth(student).post(
        f"{LESSONS_URL}{with_questions.id}/check/submit/",
        {"answers": {"0": 0}},
        format="json",
    )

    assert response.data["score"] == 50
    assert response.data["results"][1]["chosen"] is None


def test_the_score_is_recorded_for_a_learner(auth, student, with_questions, enrolled):
    from apps.learning.models import LessonProgress

    auth(student).post(
        f"{LESSONS_URL}{with_questions.id}/check/submit/",
        {"answers": {"0": 0, "1": 1}},
        format="json",
    )

    progress = LessonProgress.objects.get(enrollment=enrolled, lesson=with_questions)
    assert progress.check_score == 100
    assert progress.check_attempts == 1


def test_the_best_attempt_stands(auth, student, with_questions, enrolled):
    """The question is whether they understand it now."""
    from apps.learning.models import LessonProgress

    client = auth(student)
    client.post(
        f"{LESSONS_URL}{with_questions.id}/check/submit/",
        {"answers": {"0": 0, "1": 1}}, format="json",
    )
    client.post(
        f"{LESSONS_URL}{with_questions.id}/check/submit/",
        {"answers": {"0": 3, "1": 3}}, format="json",
    )

    progress = LessonProgress.objects.get(enrollment=enrolled, lesson=with_questions)
    assert progress.check_score == 100
    assert progress.check_attempts == 2


def test_an_authors_preview_is_not_recorded_as_a_learners_attempt(
    auth, employer, with_questions
):
    """Otherwise the author's own testing shows up in their course analytics."""
    from apps.learning.models import LessonProgress

    auth(employer).post(
        f"{LESSONS_URL}{with_questions.id}/check/submit/",
        {"answers": {"0": 0, "1": 1}},
        format="json",
    )

    assert not LessonProgress.objects.filter(lesson=with_questions).exists()


# -- the lesson changing underneath ----------------------------------------
def test_answers_are_refused_if_the_lesson_changed_since(
    auth, student, with_questions, enrolled
):
    """Otherwise the learner is marked against questions they never saw."""
    with_questions.transcript = "A completely rewritten transcript. " * 20
    with_questions.save(update_fields=["transcript"])

    response = auth(student).post(
        f"{LESSONS_URL}{with_questions.id}/check/submit/",
        {"answers": {"0": 0, "1": 1}},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["stale"] is True


# -- when there is nothing to ask about ------------------------------------
def test_a_lesson_with_no_text_offers_no_questions(auth, student, lesson):
    """The video-only case: nothing was read, so nothing can be asked."""
    lesson.transcript = ""
    lesson.video_url = "https://youtu.be/dQw4w9WgXcQ"
    lesson.save(update_fields=["transcript", "video_url"])

    response = auth(student).post(f"{LESSONS_URL}{lesson.id}/check/")

    assert response.data["available"] is False
    assert response.data["reason"] == "no_text"
    assert response.data["questions"] == []


def test_submitting_without_questions_is_refused(auth, student, lesson, enrolled):
    response = auth(student).post(
        f"{LESSONS_URL}{lesson.id}/check/submit/", {"answers": {}}, format="json"
    )

    assert response.status_code == 400


def test_a_locked_lesson_offers_no_questions(auth, student, course, db):
    from apps.learning.models import CourseModule, Lesson

    module = CourseModule.objects.create(course=course, title="Locked", order=1)
    locked = Lesson.objects.create(
        module=module, title="Locked", order=0, is_free_preview=False,
        transcript="Secret material at length. " * 30,
    )

    response = auth(student).post(f"{LESSONS_URL}{locked.id}/check/")

    assert response.status_code in {403, 404}


# -- what the model returns is not trusted blindly -------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        '{"questions": [{"question": "", "options": ["a", "b"], "answer": 0}]}',
        '{"questions": [{"question": "Q", "options": ["only one"], "answer": 0}]}',
        '{"questions": [{"question": "Q", "options": ["a", "b"], "answer": 7}]}',
        '{"questions": [{"question": "Q", "options": ["a", "b"], "answer": "first"}]}',
    ],
)
def test_malformed_questions_are_dropped_rather_than_shown(raw):
    """A broken question renders as a widget nobody can answer."""
    from apps.learning.recap import _parse_questions

    assert _parse_questions(raw) == []


def test_json_wrapped_in_a_code_fence_is_still_read():
    """Models add fences whatever the prompt says."""
    from apps.learning.recap import _parse_questions

    raw = (
        "Here you go:\n```json\n"
        '{"questions": [{"question": "Q", "options": ["a", "b"], "answer": 1, '
        '"why": "because"}]}\n```'
    )

    parsed = _parse_questions(raw)
    assert len(parsed) == 1
    # Asserted by option, not by index: the options are shuffled on the way in,
    # so pinning the index would make this test pass three times in four.
    assert parsed[0]["options"][parsed[0]["answer"]] == "b"


def test_the_correct_option_is_not_always_first(monkeypatch):
    """A check that rewards answering (a) every time measures nothing.

    Found in the first live run: the model put the right answer first in all
    three questions, so a learner picking (a) throughout scored 100%. Prompting
    does not reliably fix that habit, so the options are shuffled on the way
    in — and the answer index has to follow them.
    """
    from apps.learning.recap import _parse_questions

    raw = (
        '{"questions": [{"question": "Q", "options": ["right", "w1", "w2", "w3"], '
        '"answer": 0, "why": "because"}]}'
    )

    positions = set()
    for _ in range(40):
        question = _parse_questions(raw)[0]
        # Whatever the shuffle did, the index must still name the right option.
        assert question["options"][question["answer"]] == "right"
        positions.add(question["answer"])

    assert len(positions) > 1, "the correct answer never moved"
