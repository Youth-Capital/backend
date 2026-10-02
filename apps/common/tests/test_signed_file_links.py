"""A private file must open from a plain link — and only for the person it was issued to.

The access token lives in the SPA's memory and travels in an Authorization
header. A browser following `<a href>` or loading `<img src>` sends no such
header, so once files moved behind the protected view every course-material
link and image in the interface answered 401. The link the API hands out now
carries a short-lived signature bound to one person and one file; the view
still asks whether that person may read the file, every time.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.common.api import files
from apps.common.enums import ModerationStatus
from apps.cv.models import PortfolioItem
from apps.learning.models import Course, CourseMaterial, CourseModule, Enrollment, Lesson

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 private bytes"


def _pdf(name="book.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


class _Request:
    """Just enough of a request for `file_url` to sign a link for someone."""

    def __init__(self, user):
        self.user = user

    def build_absolute_uri(self, path):
        return f"http://testserver{path}"


def _link(kind, obj, user):
    return files.file_url(kind, obj, _Request(user)).removeprefix("http://testserver")


@pytest.fixture
def course(db, employer, admin_user):
    course = Course.objects.create(
        title="Paid", slug="paid-signed", author=admin_user,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )
    module = CourseModule.objects.create(course=course, title="M", order=0)
    lesson = Lesson.objects.create(module=module, title="L", order=0, is_free_preview=False)
    course.material = CourseMaterial.objects.create(
        course=course, lesson=lesson, title="Book", file=_pdf()
    )
    return course


@pytest.fixture
def enrolled(student, course):
    Enrollment.objects.create(user=student, course=course)
    return student


def test_the_link_the_api_hands_out_opens_without_a_header(api, enrolled, course):
    link = _link("course-material", course.material, enrolled)

    response = api.get(link)  # no Authorization header, as a browser would do

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF


def test_the_lesson_api_returns_a_link_that_opens(auth, api, enrolled, course):
    lesson = course.material.lesson
    body = auth(enrolled).get(f"/api/v1/learning/lessons/{lesson.id}/").json()
    link = body["materials"][0]["file_url"].removeprefix("http://testserver")

    api.credentials()  # drop the header: the browser will not send one
    assert api.get(link).status_code == 200


def test_a_link_opens_only_the_file_it_was_issued_for(api, student, enrolled, course):
    other = PortfolioItem.objects.create(user=student, title="w", type="PROJECT", file=_pdf())
    token = _link("course-material", course.material, enrolled).split("?t=", 1)[1]

    response = api.get(f"/api/v1/files/portfolio-item/{other.pk}/?t={token}")

    assert response.status_code == 404


def test_a_tampered_link_is_refused(api, enrolled, course):
    link = _link("course-material", course.material, enrolled)

    assert api.get(link[:-3] + "xyz").status_code == 404


def _later(monkeypatch, seconds):
    """Move the clock the signature is checked against forward."""
    import time

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + seconds)


def test_an_expired_link_is_refused(api, enrolled, course, monkeypatch):
    """Course materials live longer than other links — a lesson video keeps
    fetching as it plays — but they still die."""
    link = _link("course-material", course.material, enrolled)
    _later(monkeypatch, files.COURSE_MATERIAL_TTL_SECONDS + 60)

    assert api.get(link).status_code == 404


def test_other_files_keep_the_short_lifetime(student, monkeypatch):
    """The longer lifetime is for course materials only; a CV-portfolio link
    copied into a chat is still dead within minutes."""
    item = PortfolioItem.objects.create(user=student, title="Work", file=_pdf())
    token = files.file_url("portfolio-item", item, _Request(student)).split("?t=", 1)[1]

    _later(monkeypatch, files.LINK_TTL_SECONDS + 60)

    assert files._link_holder(token, "portfolio-item", item.pk) is None


def test_losing_access_kills_a_link_already_issued(api, enrolled, course):
    link = _link("course-material", course.material, enrolled)
    Enrollment.objects.filter(user=enrolled).delete()

    assert api.get(link).status_code == 404


def test_a_link_cannot_be_borrowed_by_a_different_signed_in_person(
    auth, enrolled, other_student, course
):
    link = _link("course-material", course.material, enrolled)

    assert auth(other_student).get(link).status_code == 404


def test_no_link_and_no_header_is_still_nothing(api, course):
    response = api.get(f"/api/v1/files/course-material/{course.material.pk}/")

    assert response.status_code in {401, 403}


def test_a_link_is_never_issued_to_somebody_signed_out(course):
    from django.contrib.auth.models import AnonymousUser

    link = files.file_url("course-material", course.material, _Request(AnonymousUser()))

    assert "?t=" not in link
