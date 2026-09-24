"""A lesson you have not paid for is not a lesson you can read.

`LessonViewSet.retrieve` asks `can_open_lesson` and always has. `list` did not:
its queryset was `Lesson.objects.select_related("module__course")` with no
filter at all, and the list serialiser is the *detail* serialiser — body,
transcript, video and attachments. So the guarded door had an unguarded window
beside it, and one request returned every lesson on the platform.

These tests pin both doors, and pin the business rule they have to leave
standing: a free preview stays free, an owner still reads their own drafts.
"""

import pytest

from apps.common.enums import ModerationStatus
from apps.learning.models import Course, CourseModule, Enrollment, Lesson

pytestmark = pytest.mark.django_db

LESSONS_URL = "/api/v1/learning/lessons/"

PAID_BODY = "The paid body nobody outside the course may read."
PREVIEW_BODY = "The free sample everybody may read."


@pytest.fixture
def course(db, employer, admin_user):
    """A published course owned by `employer`, with a locked and a free lesson."""
    course = Course.objects.create(
        title="Threat modelling",
        slug="threat-modelling",
        author=admin_user,
        employer=employer.employer_profile,
        status=ModerationStatus.PUBLISHED,
    )
    module = CourseModule.objects.create(course=course, title="Module 1", order=0)
    course.preview = Lesson.objects.create(
        module=module, title="Preview", content=PREVIEW_BODY, order=0, is_free_preview=True
    )
    course.locked = Lesson.objects.create(
        module=module, title="Locked", content=PAID_BODY, order=1, is_free_preview=False
    )
    return course


@pytest.fixture
def draft_course(db, other_employer, admin_user):
    """An unpublished course belonging to somebody else entirely."""
    course = Course.objects.create(
        title="Unreleased",
        slug="unreleased",
        author=admin_user,
        employer=other_employer.employer_profile,
        status=ModerationStatus.DRAFT,
    )
    module = CourseModule.objects.create(course=course, title="M", order=0)
    course.preview = Lesson.objects.create(
        module=module, title="Draft preview", content="Unreleased draft", order=0,
        is_free_preview=True,
    )
    course.locked = Lesson.objects.create(
        module=module, title="Draft locked", content="Unreleased draft", order=1,
        is_free_preview=False,
    )
    return course


def _titles(response):
    return {row["title"] for row in response.data["results"]}


def _bodies(response):
    return " ".join(str(row.get("content") or "") for row in response.data["results"])


# ---------------------------------------------------------------------------
# The window beside the guarded door
# ---------------------------------------------------------------------------
def test_the_list_does_not_hand_out_paid_lessons(auth, student, course):
    response = auth(student).get(LESSONS_URL)

    assert response.status_code == 200, response.data
    assert "Locked" not in _titles(response)
    assert PAID_BODY not in _bodies(response), "the paid body was served in the list"


def test_the_list_still_offers_the_free_preview(auth, student, course):
    """The paywall has a deliberate hole in it. It must stay open."""
    response = auth(student).get(LESSONS_URL)

    assert "Preview" in _titles(response)
    assert PREVIEW_BODY in _bodies(response)


def test_reading_a_paid_lesson_directly_is_refused(auth, student, course):
    response = auth(student).get(f"{LESSONS_URL}{course.locked.id}/")

    assert response.status_code in {403, 404}, response.data
    assert PAID_BODY not in str(response.data)


def test_reading_the_free_preview_directly_still_works(auth, student, course):
    response = auth(student).get(f"{LESSONS_URL}{course.preview.id}/")

    assert response.status_code == 200, response.data
    assert response.data["content"] == PREVIEW_BODY


def test_enrolling_opens_the_paid_lessons(auth, student, course):
    Enrollment.objects.create(user=student, course=course)

    listed = auth(student).get(LESSONS_URL)
    assert "Locked" in _titles(listed)

    detail = auth(student).get(f"{LESSONS_URL}{course.locked.id}/")
    assert detail.status_code == 200
    assert detail.data["content"] == PAID_BODY


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
def test_a_stranger_cannot_list_an_unpublished_course(auth, student, draft_course):
    response = auth(student).get(LESSONS_URL)

    assert _titles(response) == set()
    assert "Unreleased draft" not in _bodies(response)


def test_a_draft_preview_is_not_public_either(auth, student, draft_course):
    """`is_free_preview` on an unpublished course promises nothing to anyone.

    A preview is a shop window. A course that is not on sale has no window.
    """
    response = auth(student).get(f"{LESSONS_URL}{draft_course.preview.id}/")

    assert response.status_code in {403, 404}, response.data


def test_a_rival_employer_cannot_list_another_companys_lessons(
    auth, employer, draft_course
):
    response = auth(employer).get(LESSONS_URL)

    assert "Draft locked" not in _titles(response)
    assert "Unreleased draft" not in _bodies(response)


# ---------------------------------------------------------------------------
# The people who should get through
# ---------------------------------------------------------------------------
def test_the_owning_company_reads_its_own_course(auth, employer, course):
    response = auth(employer).get(LESSONS_URL)

    assert {"Preview", "Locked"} <= _titles(response)


def test_the_owning_company_reads_its_own_draft(auth, other_employer, draft_course):
    response = auth(other_employer).get(LESSONS_URL)

    assert {"Draft preview", "Draft locked"} <= _titles(response)


def test_an_admin_reads_everything(auth, admin_user, course, draft_course):
    response = auth(admin_user).get(LESSONS_URL)

    assert {"Preview", "Locked", "Draft preview", "Draft locked"} <= _titles(response)


# ---------------------------------------------------------------------------
# Writing is a separate question and stays shut
# ---------------------------------------------------------------------------
def test_a_student_cannot_edit_a_lesson(auth, student, course):
    response = auth(student).patch(
        f"{LESSONS_URL}{course.locked.id}/", {"content": "defaced"}, format="json"
    )

    assert response.status_code in {403, 404}, response.data
    course.locked.refresh_from_db()
    assert course.locked.content == PAID_BODY


def test_an_enrolled_student_still_cannot_edit_a_lesson(auth, student, course):
    """Reading access is not writing access."""
    Enrollment.objects.create(user=student, course=course)

    response = auth(student).patch(
        f"{LESSONS_URL}{course.locked.id}/", {"content": "defaced"}, format="json"
    )

    assert response.status_code in {403, 404}, response.data
    course.locked.refresh_from_db()
    assert course.locked.content == PAID_BODY


def test_a_rival_employer_cannot_delete_a_lesson(auth, employer, draft_course):
    response = auth(employer).delete(f"{LESSONS_URL}{draft_course.locked.id}/")

    assert response.status_code in {403, 404}, response.data
    assert Lesson.objects.filter(id=draft_course.locked.id).exists()
