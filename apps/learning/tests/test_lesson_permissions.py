"""Who may change a lesson.

`LessonViewSet` is a ModelViewSet, so `create`, `update` and `destroy` arrived
for free behind nothing but `IsAuthenticated`. Reading a lesson body was
carefully gated — enrol, or it must be a free preview — while writing one was
open to every signed-in account on the platform.

Proven before it was closed: a student PATCHed a lesson title (200), pointed
its video at another domain (200), and deleted the lesson (204).

The last one is why the delete test matters most. A defaced title is visible;
a lesson that is simply gone looks like it was never written, and the course it
belonged to is quietly shorter for everyone enrolled.
"""

import pytest

from apps.common.enums import ModerationStatus, Role

pytestmark = pytest.mark.django_db

LESSONS_URL = "/api/v1/learning/lessons/"


@pytest.fixture
def course(db, employer):
    from apps.learning.models import Course, CourseModule, Lesson

    course = Course.objects.create(
        title="Python Fundamentals",
        slug="python-fundamentals",
        author=employer,
        employer=employer.employer_profile,
        status=ModerationStatus.PUBLISHED,
    )
    module = CourseModule.objects.create(course=course, title="Syntax", order=0)
    lesson = Lesson.objects.create(
        module=module,
        title="Syntax — part 1",
        content="Original lesson text.",
        order=0,
        is_free_preview=True,
    )
    return course, module, lesson


# -- what a learner must not be able to do ---------------------------------
def test_a_student_cannot_rewrite_a_lesson(auth, student, course):
    _, _, lesson = course

    response = auth(student).patch(
        f"{LESSONS_URL}{lesson.id}/", {"title": "Defaced"}, format="json"
    )

    assert response.status_code in {403, 404}, response.data
    lesson.refresh_from_db()
    assert lesson.title == "Syntax — part 1"


def test_a_student_cannot_repoint_the_video(auth, student, course):
    """The nastiest of the three: the lesson still looks right."""
    _, _, lesson = course

    response = auth(student).patch(
        f"{LESSONS_URL}{lesson.id}/",
        {"video_url": "https://evil.example/watch"},
        format="json",
    )

    assert response.status_code in {403, 404}
    lesson.refresh_from_db()
    assert lesson.video_url == ""


def test_a_student_cannot_delete_a_lesson(auth, student, course):
    from apps.learning.models import Lesson

    _, _, lesson = course

    response = auth(student).delete(f"{LESSONS_URL}{lesson.id}/")

    assert response.status_code in {403, 404}
    assert Lesson.objects.filter(id=lesson.id).exists(), "the lesson is gone"


def test_a_student_cannot_add_a_lesson(auth, student, course):
    _, module, _ = course

    response = auth(student).post(
        LESSONS_URL,
        {"module": str(module.id), "title": "Injected", "content": "x"},
        format="json",
    )

    assert response.status_code in {400, 403, 404}


def test_a_rival_employer_cannot_touch_another_companys_lesson(
    auth, other_employer, course
):
    _, _, lesson = course

    response = auth(other_employer).patch(
        f"{LESSONS_URL}{lesson.id}/", {"title": "Ours now"}, format="json"
    )

    assert response.status_code in {403, 404}
    lesson.refresh_from_db()
    assert lesson.title == "Syntax — part 1"


def test_a_lesson_cannot_be_moved_into_a_course_you_cannot_edit(
    auth, employer, other_employer, course, db
):
    """Otherwise the ownership check is a formality: move, then edit."""
    from apps.learning.models import Course, CourseModule

    theirs = Course.objects.create(
        title="Their course",
        slug="their-course",
        author=other_employer,
        employer=other_employer.employer_profile,
        status=ModerationStatus.PUBLISHED,
    )
    their_module = CourseModule.objects.create(course=theirs, title="M", order=0)
    _, _, lesson = course

    response = auth(employer).patch(
        f"{LESSONS_URL}{lesson.id}/", {"module": str(their_module.id)}, format="json"
    )

    assert response.status_code in {403, 404}
    lesson.refresh_from_db()
    assert lesson.module.course_id != theirs.id


# -- what the author must still be able to do ------------------------------
def test_the_author_can_edit_their_own_lesson(auth, employer, course):
    """Closing the hole must not close the door."""
    _, _, lesson = course

    response = auth(employer).patch(
        f"{LESSONS_URL}{lesson.id}/",
        {"video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        format="json",
    )

    assert response.status_code == 200, response.data
    lesson.refresh_from_db()
    assert "dQw4w9WgXcQ" in lesson.video_url


def test_an_admin_can_edit_any_lesson(auth, admin_user, course):
    _, _, lesson = course

    response = auth(admin_user).patch(
        f"{LESSONS_URL}{lesson.id}/", {"title": "Fixed by moderation"}, format="json"
    )

    assert response.status_code == 200, response.data


# -- the embed the author's link produces ----------------------------------
def test_the_lesson_hands_the_page_a_checked_embed(auth, employer, course):
    """The page must never build a frame address out of the raw field."""
    _, _, lesson = course
    lesson.video_url = "https://youtu.be/dQw4w9WgXcQ?t=30"
    lesson.save(update_fields=["video_url"])

    response = auth(employer).get(f"{LESSONS_URL}{lesson.id}/")

    video = response.data["video"]
    assert video["provider"] == "youtube"
    assert video["embed_url"].startswith("https://www.youtube-nocookie.com/embed/")
    assert "start=30" in video["embed_url"]
    # The author's own text is kept, because that is what they will edit next.
    assert response.data["video_url"] == "https://youtu.be/dQw4w9WgXcQ?t=30"


def test_an_unrecognised_link_produces_no_embed(auth, employer, course):
    _, _, lesson = course
    lesson.video_url = "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ"
    lesson.save(update_fields=["video_url"])

    response = auth(employer).get(f"{LESSONS_URL}{lesson.id}/")

    assert response.data["video"]["embed_url"] is None
