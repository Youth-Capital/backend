"""Building a course: modules, lessons, materials — and the revision recap.

Authoring had no endpoints at all. A course could be created and then never
filled in: modules and materials did not exist, and lessons were writable by
anybody signed in. So these tests cover a surface that is new, and the shape
of them follows from one rule — none of these things is owned separately from
the course they belong to, so all of them answer to the course's edit rule.

The recap tests are the ones that matter most, and not for permissions. The
platform cannot hear a YouTube video: it holds a link. A recap must therefore
be built from text a person actually wrote, and when there is none the answer
has to say so. A model asked to summarise a video it never watched will write
something fluent and wrong, and a learner revising a security lesson from it
would not be able to tell.
"""

import io

import pytest

from apps.common.enums import ModerationStatus

pytestmark = pytest.mark.django_db

MODULES_URL = "/api/v1/learning/modules/"
MATERIALS_URL = "/api/v1/learning/materials/"
LESSONS_URL = "/api/v1/learning/lessons/"


@pytest.fixture
def course(db, employer):
    from apps.learning.models import Course

    return Course.objects.create(
        title="Web Security Basics", slug="web-security-basics", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )


@pytest.fixture
def module(db, course):
    from apps.learning.models import CourseModule

    return CourseModule.objects.create(course=course, title="Threats", order=0)


@pytest.fixture
def lesson(db, module):
    from apps.learning.models import Lesson

    return Lesson.objects.create(
        module=module, title="Threat landscape", order=0, is_free_preview=True
    )


def pdf(name="reading.pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, b"%PDF-1.7\n%stub\n", content_type="application/pdf")


# -- modules ---------------------------------------------------------------
def test_the_author_can_add_a_module(auth, employer, course):
    response = auth(employer).post(
        MODULES_URL, {"course": str(course.id), "title": "OWASP Top 10", "order": 1},
        format="json",
    )

    assert response.status_code == 201, response.data


def test_a_rival_cannot_add_a_module_to_your_course(auth, other_employer, course):
    response = auth(other_employer).post(
        MODULES_URL, {"course": str(course.id), "title": "Theirs"}, format="json"
    )

    assert response.status_code in {403, 404}


def test_a_student_cannot_add_a_module(auth, student, course):
    response = auth(student).post(
        MODULES_URL, {"course": str(course.id), "title": "Mine now"}, format="json"
    )

    assert response.status_code in {403, 404}


# -- materials -------------------------------------------------------------
def test_a_link_can_be_attached_to_a_lesson(auth, employer, course, lesson):
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "LINK",
            "title": "OWASP Cheat Sheet",
            "url": "https://cheatsheetseries.owasp.org/",
        },
        format="json",
    )

    assert response.status_code == 201, response.data


def test_a_book_needs_a_link_not_a_file(auth, employer, course):
    """A book is a pointer at something the platform does not host."""
    response = auth(employer).post(
        MATERIALS_URL,
        {"course": str(course.id), "kind": "BOOK", "title": "Clean Code"},
        format="json",
    )

    assert response.status_code == 400, response.data


def test_a_file_material_needs_a_file(auth, employer, course):
    response = auth(employer).post(
        MATERIALS_URL,
        {"course": str(course.id), "kind": "FILE", "title": "Worksheet"},
        format="json",
    )

    assert response.status_code == 400, response.data


def test_an_uploaded_file_is_validated(auth, employer, course):
    """Uploads are where a stranger hands the server a file."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "kind": "FILE",
            "title": "Definitely a pdf",
            "file": SimpleUploadedFile(
                "payload.pdf", b"MZ\x90\x00 not a pdf", content_type="application/pdf"
            ),
        },
        format="multipart",
    )

    assert response.status_code == 400, response.data


def test_a_real_pdf_is_accepted(auth, employer, course):
    response = auth(employer).post(
        MATERIALS_URL,
        {"course": str(course.id), "kind": "FILE", "title": "Reading", "file": pdf()},
        format="multipart",
    )

    assert response.status_code == 201, response.data


def test_a_lesson_from_another_course_is_refused(
    auth, employer, other_employer, course, db
):
    """Otherwise a material lands in a course it does not belong to."""
    from apps.learning.models import Course, CourseModule, Lesson

    other = Course.objects.create(
        title="Other", slug="other", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )
    other_module = CourseModule.objects.create(course=other, title="M", order=0)
    stray = Lesson.objects.create(module=other_module, title="Stray", order=0)

    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(stray.id),
            "kind": "LINK",
            "title": "Mismatched",
            "url": "https://example.com",
        },
        format="json",
    )

    assert response.status_code == 400, response.data


def test_materials_cannot_be_listed_without_naming_a_course(auth, employer):
    """Otherwise the endpoint hands out every company's worksheets."""
    response = auth(employer).get(MATERIALS_URL)

    assert response.status_code == 400


def test_a_stranger_cannot_read_the_materials_of_a_course_they_are_not_in(
    auth, other_employer, employer, course
):
    from apps.learning.models import CourseMaterial

    CourseMaterial.objects.create(
        course=course, kind="LINK", title="Reading", url="https://example.com"
    )

    response = auth(other_employer).get(f"{MATERIALS_URL}?course={course.id}")

    assert response.status_code in {403, 404}


def test_an_enrolled_learner_can_read_the_materials(auth, student, employer, course):
    from apps.learning.models import CourseMaterial, Enrollment

    CourseMaterial.objects.create(
        course=course, kind="LINK", title="Reading", url="https://example.com"
    )
    Enrollment.objects.create(user=student, course=course)

    response = auth(student).get(f"{MATERIALS_URL}?course={course.id}")

    assert response.status_code == 200, response.data
    assert len(response.data["results"]) == 1


def test_a_learner_cannot_add_a_material(auth, student, course):
    from apps.learning.models import Enrollment

    Enrollment.objects.create(user=student, course=course)

    response = auth(student).post(
        MATERIALS_URL,
        {"course": str(course.id), "kind": "LINK", "title": "Mine", "url": "https://x.uz"},
        format="json",
    )

    assert response.status_code in {403, 404}


# -- the recap -------------------------------------------------------------
def test_a_lesson_with_no_text_cannot_be_recapped(auth, student, lesson):
    """The case that matters: a video link and nothing else.

    The platform never heard the video. Saying so is the only honest answer;
    a model would happily summarise the title instead.
    """
    lesson.video_url = "https://youtu.be/dQw4w9WgXcQ"
    lesson.save(update_fields=["video_url"])

    response = auth(student).post(f"{LESSONS_URL}{lesson.id}/recap/")

    assert response.status_code == 200, response.data
    assert response.data["available"] is False
    assert response.data["reason"] == "no_text"
    assert response.data["recap"] == ""


def test_a_lesson_with_a_scrap_of_text_is_not_recapped_either(auth, student, lesson):
    """A heading is already shorter than any summary of it."""
    lesson.content = "Intro."
    lesson.save(update_fields=["content"])

    response = auth(student).post(f"{LESSONS_URL}{lesson.id}/recap/")

    assert response.data["available"] is False
    assert response.data["reason"] == "too_short"


def test_a_transcript_is_what_gets_summarised(auth, student, lesson, monkeypatch):
    """Stubbed: what is pinned is that the model receives the author's text."""
    from apps.learning import recap as recap_module

    lesson.transcript = "Cross-site scripting happens when. " * 40
    lesson.save(update_fields=["transcript"])

    seen = {}

    def fake_build(lesson_arg, *, language):
        seen["body"] = recap_module.recap_source(lesson_arg)
        seen["language"] = language
        return "Кратко: про XSS."

    monkeypatch.setattr("apps.learning.recap.build_recap", fake_build)

    response = auth(student).post(
        f"{LESSONS_URL}{lesson.id}/recap/", headers={"accept-language": "ru"}
    )

    assert response.status_code == 200, response.data
    assert response.data["available"] is True
    assert response.data["recap"] == "Кратко: про XSS."
    assert "Cross-site scripting" in seen["body"]
    assert seen["language"] == "Russian"


def test_the_recap_is_cached_until_the_lesson_changes(auth, student, lesson, monkeypatch):
    """A model call per learner per revision is a bill for the same sentences."""
    calls = {"n": 0}

    def fake_build(lesson_arg, *, language):
        calls["n"] += 1
        return f"recap {calls['n']}"

    monkeypatch.setattr("apps.learning.recap.build_recap", fake_build)

    lesson.transcript = "A long enough transcript about security. " * 20
    lesson.save(update_fields=["transcript"])

    first = auth(student).post(f"{LESSONS_URL}{lesson.id}/recap/")
    second = auth(student).post(f"{LESSONS_URL}{lesson.id}/recap/")

    assert first.data["recap"] == second.data["recap"]
    assert second.data["cached"] is True
    assert calls["n"] == 1, "the model was asked twice for the same text"


def test_editing_the_lesson_invalidates_the_recap(auth, student, lesson, monkeypatch):
    """A stale recap describes a lesson that no longer exists."""
    calls = {"n": 0}

    def fake_build(lesson_arg, *, language):
        calls["n"] += 1
        return f"recap {calls['n']}"

    monkeypatch.setattr("apps.learning.recap.build_recap", fake_build)

    lesson.transcript = "First version of the transcript, long enough. " * 20
    lesson.save(update_fields=["transcript"])
    auth(student).post(f"{LESSONS_URL}{lesson.id}/recap/")

    lesson.refresh_from_db()
    lesson.transcript = "Rewritten transcript, also long enough to count. " * 20
    lesson.save(update_fields=["transcript"])

    response = auth(student).post(f"{LESSONS_URL}{lesson.id}/recap/")

    assert response.data["cached"] is False
    assert calls["n"] == 2


def test_a_locked_lesson_cannot_be_recapped(auth, student, module, db):
    """The recap is lesson content: it answers to the same gate."""
    from apps.learning.models import Lesson

    locked = Lesson.objects.create(
        module=module, title="Locked", order=1, is_free_preview=False,
        transcript="Secret material, at length. " * 30,
    )

    response = auth(student).post(f"{LESSONS_URL}{locked.id}/recap/")

    assert response.status_code in {403, 404}
