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


def png(name="diagram.png", size=(8, 8)):
    """A real PNG, encoded here rather than checked in.

    The image validator decodes the bytes with Pillow, so a stub of the kind
    pdf() gets away with would be rejected for the right reason and prove
    nothing at all about the path being tested.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 182, 255)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


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


def test_a_book_needs_either_a_file_or_a_link(auth, employer, course):
    """A title on its own is a book nobody can open."""
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


# -- lesson materials: video and images ------------------------------------
#
# The two kinds added for lesson materials are the two that are not simply a
# link with a different label. A VIDEO goes in an iframe, so what makes it a
# VIDEO is that the link survived apps/learning/video.py. An IMAGE is bytes we
# host and show inline, so it answers to the image validator rather than the
# document one.
def test_a_youtube_link_can_be_attached_to_a_lesson_as_a_video(
    auth, employer, course, lesson
):
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "VIDEO",
            "title": "How TLS works",
            "url": "https://youtu.be/dQw4w9WgXcQ?t=42",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    video = response.data["video"]
    # The embed is assembled by us, never echoed back from what was pasted.
    assert video["provider"] == "youtube"
    assert video["embed_url"].startswith("https://www.youtube-nocookie.com/embed/")
    assert "start=42" in video["embed_url"]


def test_a_video_that_cannot_be_embedded_is_refused_not_downgraded(
    auth, employer, course, lesson
):
    """A look-alike host must not reach an iframe.

    Refused rather than quietly stored as a LINK: an author who picked "video"
    expects a player, and a silent downgrade is a surprise found later by a
    learner instead of now by the person who can still fix it.
    """
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "VIDEO",
            "title": "Not really youtube",
            "url": "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        },
        format="json",
    )

    assert response.status_code == 400, response.data
    # The project wraps errors in an envelope; the field is what matters here,
    # because the author needs to be told which box to fix.
    assert "url" in response.data["error"]["details"]


def test_an_image_material_is_accepted_and_carries_no_embed(
    auth, employer, course, lesson
):
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "IMAGE",
            "title": "Handshake diagram",
            "file": png(),
        },
        format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["file_url"]
    assert response.data["video"] is None


def test_an_image_material_is_checked_by_the_image_validator(
    auth, employer, course, lesson
):
    """A .png that is not a PNG is stored XSS the moment it is served back."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "IMAGE",
            "title": "Definitely a png",
            "file": SimpleUploadedFile(
                "payload.png", b"<html>not a png</html>", content_type="image/png"
            ),
        },
        format="multipart",
    )

    assert response.status_code == 400, response.data


def test_an_image_material_needs_a_file_not_a_link(auth, employer, course, lesson):
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "IMAGE",
            "title": "Diagram",
            "url": "https://example.com/diagram.png",
        },
        format="json",
    )

    assert response.status_code == 400, response.data


def test_lesson_materials_can_be_listed_for_one_lesson(
    auth, employer, course, lesson, module
):
    """The lesson filter is what makes these lesson materials at all.

    A course-wide reading list and a worksheet for lesson three are different
    things, and the editor asks for one lesson at a time.
    """
    from apps.learning.models import CourseMaterial, Lesson

    other = Lesson.objects.create(module=module, title="Later", order=1)
    CourseMaterial.objects.create(
        course=course, lesson=lesson, kind="LINK", title="Mine",
        url="https://example.com/a",
    )
    CourseMaterial.objects.create(
        course=course, lesson=other, kind="LINK", title="Theirs",
        url="https://example.com/b",
    )
    CourseMaterial.objects.create(
        course=course, lesson=None, kind="LINK", title="Course-wide",
        url="https://example.com/c",
    )

    response = auth(employer).get(
        f"{MATERIALS_URL}?course={course.id}&lesson={lesson.id}"
    )

    assert response.status_code == 200, response.data
    titles = [row["title"] for row in response.data["results"]]
    assert titles == ["Mine"]


def test_a_material_cannot_be_pinned_to_another_courses_lesson(
    auth, employer, course, lesson
):
    """The lesson has to belong to the course the material claims."""
    from apps.learning.models import Course, CourseModule, Lesson

    other_course = Course.objects.create(
        title="Something else", slug="something-else", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )
    other_module = CourseModule.objects.create(
        course=other_course, title="M", order=0
    )
    stranger = Lesson.objects.create(module=other_module, title="Stranger", order=0)

    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(stranger.id),
            "kind": "LINK",
            "title": "Wrong course",
            "url": "https://example.com/",
        },
        format="json",
    )

    assert response.status_code == 400, response.data


# -- books: a file when the author has one, a link when they do not ---------
#
# A book began as a link only. That is right for a citation and wrong for an
# author with the PDF on their desktop, so it now takes either -- with its own
# validator and its own size cap, because a book is not a worksheet.
def epub(name="book.epub"):
    """A minimal EPUB: a ZIP whose first entry is an uncompressed mimetype.

    Built rather than stubbed, because the validator does not settle for the
    ZIP magic number — a .epub that is a zip of anything would pass that and
    then fail to open for every learner who downloaded it.
    """
    import zipfile

    from django.core.files.uploadedfile import SimpleUploadedFile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", "<container/>")
    return SimpleUploadedFile(
        name, buffer.getvalue(), content_type="application/epub+zip"
    )


def test_a_book_can_be_uploaded_as_a_pdf(auth, employer, course, lesson):
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "BOOK",
            "title": "Clean Code",
            "file": pdf("clean-code.pdf"),
        },
        format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["file_url"]


def test_a_book_can_be_uploaded_as_an_epub(auth, employer, course):
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "kind": "BOOK",
            "title": "The Pragmatic Programmer",
            "file": epub(),
        },
        format="multipart",
    )

    assert response.status_code == 201, response.data
    assert response.data["file_url"]


def test_a_zip_renamed_to_epub_is_refused(auth, employer, course):
    """The ZIP magic number alone proves nothing about an EPUB."""
    import zipfile

    from django.core.files.uploadedfile import SimpleUploadedFile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("something.txt", "not a book")

    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "kind": "BOOK",
            "title": "Definitely a book",
            "file": SimpleUploadedFile(
                "payload.epub", buffer.getvalue(), content_type="application/epub+zip"
            ),
        },
        format="multipart",
    )

    assert response.status_code == 400, response.data


def test_a_book_can_still_be_a_link(auth, employer, course):
    """A citation is a book the platform does not have, and that is fine."""
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "kind": "BOOK",
            "title": "Clean Code, chapter 3",
            "url": "https://example.com/clean-code",
        },
        format="json",
    )

    assert response.status_code == 201, response.data


def test_a_book_may_be_bigger_than_an_ordinary_upload(auth, employer, course):
    """5 MB is the worksheet cap and is not a book.

    Sized just over the general limit and well under the book one, so this
    fails the moment somebody points books back at MAX_UPLOAD_SIZE_MB.
    """
    from django.conf import settings
    from django.core.files.uploadedfile import SimpleUploadedFile

    over_general = (settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024) + 1024
    assert over_general < settings.MAX_BOOK_SIZE_MB * 1024 * 1024

    payload = b"%PDF-1.7\n" + (b"0" * over_general)
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "kind": "BOOK",
            "title": "A long one",
            "file": SimpleUploadedFile(
                "long.pdf", payload, content_type="application/pdf"
            ),
        },
        format="multipart",
    )

    assert response.status_code == 201, response.data


def test_a_worksheet_is_still_held_to_the_ordinary_cap(auth, employer, course):
    """Raising the book cap must not have raised everything else's."""
    from django.conf import settings
    from django.core.files.uploadedfile import SimpleUploadedFile

    payload = b"%PDF-1.7\n" + (
        b"0" * ((settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024) + 1024)
    )
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "kind": "FILE",
            "title": "Worksheet",
            "file": SimpleUploadedFile(
                "worksheet.pdf", payload, content_type="application/pdf"
            ),
        },
        format="multipart",
    )

    assert response.status_code == 400, response.data
