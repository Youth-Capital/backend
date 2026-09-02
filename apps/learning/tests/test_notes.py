"""Study notes: private, owned, and tied to the right lesson.

The interesting properties here are not "can a note be saved" — it is whether
one student can reach another's writing, whether a note can claim a lesson it
does not belong to, and whether the note body can be used to keep course
content the student never had access to.
"""

import pytest
from rest_framework.test import APIClient

from apps.common.enums import ModerationStatus
from apps.learning.models import Course, CourseModule, Enrollment, Lesson, LessonNote

pytestmark = pytest.mark.django_db

NOTES = "/api/v1/learning/my/notes/"


def errors(response) -> dict:
    """Field errors out of the project's error envelope."""
    return response.data["error"]["details"]


def code(response) -> str:
    return response.data["error"]["code"]


def make_course(title="Web Security Basics", slug="web-sec", lessons=2, free=False):
    course = Course.objects.create(
        slug=slug, title=title, status=ModerationStatus.PUBLISHED
    )
    module = CourseModule.objects.create(course=course, title="Module 1")
    for order in range(lessons):
        Lesson.objects.create(
            module=module,
            title=f"Lesson {order + 1}",
            order=order,
            is_free_preview=free and order == 0,
        )
    return course


@pytest.fixture
def course():
    return make_course()


@pytest.fixture
def enrolled(student, course):
    Enrollment.objects.create(user=student, course=course)
    return student


def note_payload(course, lesson=None, **overrides):
    payload = {
        "course": str(course.id),
        "title": "TCPDump flags",
        "content": "-n stops name resolution, which is on by default.",
    }
    if lesson is not None:
        payload["lesson"] = str(lesson.id)
    payload.update(overrides)
    return payload


# -- writing ---------------------------------------------------------------
def test_a_note_is_saved_against_the_lesson_being_studied(auth, enrolled, course):
    lesson = course.modules.first().lessons.first()

    response = auth(enrolled).post(NOTES, note_payload(course, lesson), format="json")

    assert response.status_code == 201, response.data
    assert str(response.data["lesson"]) == str(lesson.id)
    assert response.data["lesson_title"] == "Lesson 1"
    assert response.data["course_title"] == "Web Security Basics"


def test_a_note_may_belong_to_the_course_rather_than_one_lesson(auth, enrolled, course):
    response = auth(enrolled).post(NOTES, note_payload(course), format="json")

    assert response.status_code == 201, response.data
    assert response.data["lesson"] is None


def test_a_note_without_a_title_is_accepted(auth, enrolled, course):
    """The lesson-page composer sends body only — a headline is friction there."""
    lesson = course.modules.first().lessons.first()

    response = auth(enrolled).post(
        NOTES, note_payload(course, lesson, title=""), format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["title"] == ""
    assert response.data["content"]


def test_an_empty_note_is_rejected(auth, enrolled, course):
    response = auth(enrolled).post(
        NOTES, note_payload(course, content="   "), format="json"
    )

    assert response.status_code == 400
    assert "content" in errors(response)


def test_a_note_cannot_claim_a_lesson_from_another_course(auth, enrolled, course):
    """Otherwise the lesson line under the note is text the student never wrote."""
    elsewhere = make_course(title="Python", slug="python")
    foreign_lesson = elsewhere.modules.first().lessons.first()

    response = auth(enrolled).post(
        NOTES, note_payload(course, foreign_lesson), format="json"
    )

    assert response.status_code == 400
    assert "lesson" in errors(response)


# -- access ----------------------------------------------------------------
def test_notes_require_the_same_access_as_the_lesson_itself(auth, student, course):
    """A note body would otherwise be a way to keep content you cannot open."""
    lesson = course.modules.first().lessons.first()

    response = auth(student).post(NOTES, note_payload(course, lesson), format="json")

    assert response.status_code == 403
    assert code(response) == "not_enrolled"


def test_a_free_preview_lesson_can_be_annotated_without_enrolling(auth, student):
    """The preview is offered to everyone, so notes on it are too."""
    course = make_course(slug="free-course", free=True)
    preview = course.modules.first().lessons.first()

    response = auth(student).post(NOTES, note_payload(course, preview), format="json")

    assert response.status_code == 201, response.data


def test_moving_a_note_to_a_course_you_cannot_open_is_refused(auth, enrolled, course):
    """Access is re-checked on edit, not trusted from creation time."""
    note = LessonNote.objects.create(user=enrolled, course=course, content="mine")
    other_course = make_course(title="Python", slug="python")

    response = auth(enrolled).patch(
        f"{NOTES}{note.id}/", {"course": str(other_course.id)}, format="json"
    )

    assert response.status_code == 403


# -- privacy ---------------------------------------------------------------
@pytest.fixture
def someone_elses_note(student, course):
    Enrollment.objects.create(user=student, course=course)
    return LessonNote.objects.create(
        user=student,
        course=course,
        lesson=course.modules.first().lessons.first(),
        title="Private",
        content="Something I wrote for myself.",
    )


def test_another_student_cannot_list_it(auth, other_student, someone_elses_note):
    response = auth(other_student).get(NOTES)

    assert response.status_code == 200
    assert response.data["results"] == []


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("get", {}),
        ("patch", {"data": {"content": "hijacked"}, "format": "json"}),
        ("delete", {}),
    ],
)
def test_another_student_cannot_reach_it_directly(
    auth, other_student, someone_elses_note, method, kwargs
):
    """404, not 403 — a forbidden response would confirm the note exists."""
    response = getattr(auth(other_student), method)(
        f"{NOTES}{someone_elses_note.id}/", **kwargs
    )

    assert response.status_code == 404
    someone_elses_note.refresh_from_db()
    assert someone_elses_note.content == "Something I wrote for myself."


def test_an_admin_has_no_special_access_to_notes(auth, admin_user, someone_elses_note):
    """Notes are not course content. Nobody moderates a student's own writing."""
    response = auth(admin_user).get(f"{NOTES}{someone_elses_note.id}/")

    assert response.status_code == 404


def test_the_owner_cannot_give_a_note_away(auth, enrolled, other_student, course):
    """`user` comes from the request, so no payload can reassign it."""
    response = auth(enrolled).post(
        NOTES, note_payload(course, user=str(other_student.id)), format="json"
    )

    assert response.status_code == 201
    assert LessonNote.objects.get(id=response.data["id"]).user_id == enrolled.id


def test_deleting_a_note_leaves_other_students_notes_alone(
    auth, enrolled, other_student, course
):
    mine = LessonNote.objects.create(user=enrolled, course=course, content="mine")
    theirs = LessonNote.objects.create(
        user=other_student, course=course, content="theirs"
    )

    response = auth(enrolled).delete(f"{NOTES}{mine.id}/")

    assert response.status_code == 204
    assert not LessonNote.objects.filter(id=mine.id).exists()
    assert LessonNote.objects.filter(id=theirs.id).exists()


# -- reading back ----------------------------------------------------------
def test_editing_moves_the_note_to_the_top_of_the_list(auth, enrolled, course):
    """The list is ordered by last change, which is what "updated" means to a reader."""
    older = LessonNote.objects.create(user=enrolled, course=course, content="first")
    newer = LessonNote.objects.create(user=enrolled, course=course, content="second")
    before = older.updated_at

    response = auth(enrolled).patch(
        f"{NOTES}{older.id}/", {"content": "first, revised"}, format="json"
    )

    assert response.status_code == 200
    older.refresh_from_db()
    assert older.updated_at > before

    listing = auth(enrolled).get(NOTES)
    assert [row["id"] for row in listing.data["results"]] == [
        str(older.id),
        str(newer.id),
    ]


def test_notes_can_be_filtered_by_course_and_lesson(auth, enrolled, course):
    lessons = list(course.modules.first().lessons.all())
    other_course = make_course(title="Python", slug="python")
    Enrollment.objects.create(user=enrolled, course=other_course)

    LessonNote.objects.create(
        user=enrolled, course=course, lesson=lessons[0], content="a"
    )
    LessonNote.objects.create(
        user=enrolled, course=course, lesson=lessons[1], content="b"
    )
    LessonNote.objects.create(user=enrolled, course=other_course, content="c")

    client = auth(enrolled)
    assert client.get(f"{NOTES}?course={course.id}").data["count"] == 2
    assert client.get(f"{NOTES}?lesson={lessons[0].id}").data["count"] == 1


def test_notes_can_be_searched(auth, enrolled, course):
    LessonNote.objects.create(
        user=enrolled, course=course, title="TCPDump", content="packet capture"
    )
    LessonNote.objects.create(
        user=enrolled, course=course, title="Nmap", content="scanning"
    )

    response = auth(enrolled).get(f"{NOTES}?search=packet")

    assert response.data["count"] == 1
    assert response.data["results"][0]["title"] == "TCPDump"


def test_a_removed_lesson_does_not_take_the_note_with_it(enrolled, course):
    """Partners resend whole courses; a syllabus edit must not delete writing."""
    lesson = course.modules.first().lessons.first()
    note = LessonNote.objects.create(
        user=enrolled, course=course, lesson=lesson, content="still mine"
    )

    lesson.delete()

    note.refresh_from_db()
    assert note.lesson_id is None
    assert note.content == "still mine"


def test_notes_need_authentication(course):
    assert APIClient().get(NOTES).status_code == 401
