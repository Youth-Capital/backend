"""Which answer is the right one is not a thing a candidate may read.

The platform sells employers "verified skills", and the verification is a test.
An answer key that any signed-in learner can fetch turns every verified badge
into a claim about nothing, so these are regression tests for the only property
that matters here: `is_correct` reaches an author, an owner and an admin, and
nobody else — on any endpoint, in any serialiser, by any route.

The leak that prompted these: `QuestionViewSet.get_queryset` filtered on
`Q(test__employer=company)`, and for a learner `company` is None, so the clause
became `employer IS NULL` — which is exactly what a platform-authored test
looks like. One signed-in student could read the key to all of them.
"""

import uuid

import pytest

# Imported under other names on purpose: pytest collects any module-level class
# called Test*, and the model classes are not tests.
from apps.assessment.models import AnswerOption, ModerationStatus, Question
from apps.assessment.models import Test as TestModel
from apps.assessment.models import TestType as TestKind

pytestmark = pytest.mark.django_db

QUESTIONS_URL = "/api/v1/assessment/questions/"


def _platform_author():
    """Somebody has to have typed the test; a platform test has no company."""
    from apps.accounts.models import User
    from apps.common.enums import Role

    return User.objects.create_user(
        email=f"author-{uuid.uuid4().hex[:8]}@test.uz",
        password="TestPass12345",
        role=Role.ADMIN,
    )


def _test_with_key(*, employer=None, author=None, title="Platform test"):
    """A test with one question whose second option is the correct one."""
    test = TestModel.objects.create(
        title=title,
        type=TestKind.SKILL_TEST,
        employer=employer,
        author=author or _platform_author(),
        status=ModerationStatus.PUBLISHED,
        is_public=True,
    )
    question = Question.objects.create(test=test, text="2 + 2 = ?", order=0)
    AnswerOption.objects.create(question=question, text="Three", is_correct=False, order=0)
    AnswerOption.objects.create(question=question, text="Four", is_correct=True, order=1)
    return test, question


def _mentions_answer_key(payload) -> bool:
    """True if `is_correct` appears anywhere in the response, at any depth."""
    if isinstance(payload, dict):
        return "is_correct" in payload or any(
            _mentions_answer_key(value) for value in payload.values()
        )
    if isinstance(payload, list):
        return any(_mentions_answer_key(item) for item in payload)
    return False


# ---------------------------------------------------------------------------
# The leak itself
# ---------------------------------------------------------------------------
def test_a_student_cannot_list_questions(auth, student):
    """The endpoint is an authoring tool. A learner has no business on it."""
    _test_with_key()

    response = auth(student).get(QUESTIONS_URL)

    assert response.status_code == 403, response.data


def test_a_student_cannot_read_one_question(auth, student):
    _, question = _test_with_key()

    response = auth(student).get(f"{QUESTIONS_URL}{question.id}/")

    assert response.status_code in {403, 404}, response.data
    assert not _mentions_answer_key(response.data)


def test_a_platform_test_is_not_treated_as_ownerless_property(auth, student):
    """The specific bug: employer=None must not mean "everybody's"."""
    _test_with_key(employer=None, title="Platform-authored")

    response = auth(student).get(QUESTIONS_URL)

    assert response.status_code == 403
    assert not _mentions_answer_key(response.data)


def test_a_student_cannot_reach_the_key_through_the_authoring_action(auth, student):
    test, _ = _test_with_key()

    response = auth(student).get(f"/api/v1/assessment/tests/{test.id}/questions/")

    assert response.status_code in {403, 404}, response.data
    assert not _mentions_answer_key(response.data)


def test_a_student_cannot_write_questions(auth, student):
    test, _ = _test_with_key()

    response = auth(student).post(
        QUESTIONS_URL, {"test": str(test.id), "text": "Injected", "order": 9}, format="json"
    )

    assert response.status_code in {403, 400}, response.data
    assert not Question.objects.filter(text="Injected").exists()


# ---------------------------------------------------------------------------
# Horizontal escalation between employers
# ---------------------------------------------------------------------------
def test_an_employer_cannot_read_another_companys_answer_key(auth, employer, other_employer):
    _test_with_key(employer=other_employer.employer_profile, title="Rival screening")

    response = auth(employer).get(QUESTIONS_URL)

    assert response.status_code == 200
    assert response.data["count"] == 0, "a rival's answer key must not be listed"


def test_an_employer_without_a_company_sees_nothing(auth, db):
    """The None trap again, from the other side.

    An employer account whose company profile has not been created yet has
    `employer_profile` None, and a filter written on that value would match
    every platform test.
    """
    from apps.accounts.models import User
    from apps.common.enums import Role

    lone = User.objects.create_user(
        email="nocompany@test.uz", password="TestPass12345", role=Role.EMPLOYER
    )
    _test_with_key(employer=None, title="Platform-authored")

    response = auth(lone).get(QUESTIONS_URL)

    assert response.status_code == 200
    assert response.data["count"] == 0


# ---------------------------------------------------------------------------
# The people who should get through
# ---------------------------------------------------------------------------
def test_an_employer_reads_the_key_for_their_own_test(auth, employer):
    _test_with_key(employer=employer.employer_profile, title="Ours")

    response = auth(employer).get(QUESTIONS_URL)

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert _mentions_answer_key(response.data), "an author must still be able to edit"


def test_an_author_reads_the_key_for_a_test_they_wrote(auth, employer):
    _test_with_key(employer=None, author=employer, title="Written by me")

    response = auth(employer).get(QUESTIONS_URL)

    assert response.status_code == 200
    assert response.data["count"] == 1


def test_an_admin_reads_everything(auth, admin_user, employer):
    _test_with_key(employer=employer.employer_profile)
    _test_with_key(employer=None, title="Platform")

    response = auth(admin_user).get(QUESTIONS_URL)

    assert response.status_code == 200
    assert response.data["count"] == 2


# ---------------------------------------------------------------------------
# Taking a test still works, and still says nothing
# ---------------------------------------------------------------------------
def test_starting_an_attempt_returns_questions_without_the_key(auth, student):
    test, _ = _test_with_key()

    response = auth(student).post(f"/api/v1/assessment/tests/{test.id}/start/")

    assert response.status_code == 201, response.data
    assert not _mentions_answer_key(response.data), "the key leaked into the taking flow"
    # And the learner can still see what they are answering.
    questions = response.data.get("questions") or []
    assert len(questions) == 1
    assert len(questions[0]["options"]) == 2
