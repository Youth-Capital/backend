"""A file belonging to one person is not readable by another.

Uploads used to be written into `MEDIA_ROOT` under the name the browser sent,
and answered by the web server with no authorisation at all. Two things follow
from that, and both are tested here: the file must no longer have a public
address, and the address it does have must ask who is asking.

The third property is quieter but matters as much — the stored name must not be
the name the uploader chose. `resume.pdf` stored as `resume.pdf` is a password
everybody already knows.
"""

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.common.enums import ModerationStatus
from apps.cv.models import PortfolioItem
from apps.learning.models import (
    Certificate,
    Course,
    CourseMaterial,
    CourseModule,
    Enrollment,
    Lesson,
)

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4 the private bytes"


def _pdf(name="resume.pdf"):
    return SimpleUploadedFile(name, PDF, content_type="application/pdf")


@pytest.fixture
def portfolio(db, student):
    return PortfolioItem.objects.create(
        user=student, title="My work", type="PROJECT", file=_pdf()
    )


@pytest.fixture
def course(db, employer, admin_user):
    course = Course.objects.create(
        title="Paid course",
        slug="paid-course",
        author=admin_user,
        employer=employer.employer_profile,
        status=ModerationStatus.PUBLISHED,
    )
    module = CourseModule.objects.create(course=course, title="M", order=0)
    course.locked_lesson = Lesson.objects.create(
        module=module, title="Locked", order=0, is_free_preview=False
    )
    course.material = CourseMaterial.objects.create(
        course=course, lesson=course.locked_lesson, title="Course book", file=_pdf("book.epub")
    )
    return course


def _url(kind, obj):
    return f"/api/v1/files/{kind}/{obj.pk}/"


# ---------------------------------------------------------------------------
# One person's file is not another's
# ---------------------------------------------------------------------------
def test_another_learner_cannot_download_a_portfolio_file(auth, other_student, portfolio):
    response = auth(other_student).get(_url("portfolio-item", portfolio))

    assert response.status_code == 404, response.status_code


def test_the_owner_can_download_their_own_file(auth, student, portfolio):
    response = auth(student).get(_url("portfolio-item", portfolio))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF


def test_an_admin_can_download_it(auth, admin_user, portfolio):
    assert auth(admin_user).get(_url("portfolio-item", portfolio)).status_code == 200


def test_a_signed_out_visitor_gets_nothing(api, portfolio):
    response = api.get(_url("portfolio-item", portfolio))

    assert response.status_code in {401, 403}


def test_an_unknown_file_kind_is_not_a_route(auth, student, portfolio):
    response = auth(student).get(f"/api/v1/files/not-a-kind/{portfolio.pk}/")

    assert response.status_code == 404


def test_refusal_looks_the_same_as_absence(auth, other_student, portfolio):
    """A 403 would confirm the id is real. Both answers are 404."""
    import uuid

    refused = auth(other_student).get(_url("portfolio-item", portfolio))
    missing = auth(other_student).get(f"/api/v1/files/portfolio-item/{uuid.uuid4()}/")

    assert refused.status_code == missing.status_code == 404


# ---------------------------------------------------------------------------
# Paid content
# ---------------------------------------------------------------------------
def test_a_stranger_cannot_download_course_material(auth, student, course):
    response = auth(student).get(_url("course-material", course.material))

    assert response.status_code == 404


def test_enrolling_opens_the_material(auth, student, course):
    Enrollment.objects.create(user=student, course=course)

    response = auth(student).get(_url("course-material", course.material))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == PDF


def test_the_owning_company_can_download_its_own_material(auth, employer, course):
    assert auth(employer).get(_url("course-material", course.material)).status_code == 200


def test_a_rival_company_cannot(auth, other_employer, course):
    assert auth(other_employer).get(_url("course-material", course.material)).status_code == 404


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------
def test_a_certificate_belongs_to_the_person_named_on_it(
    auth, student, other_student, course
):
    certificate = Certificate.objects.create(
        user=student,
        course=course,
        verification_code="ABC123",
        file=_pdf("certificate.pdf"),
    )

    assert auth(student).get(_url("certificate", certificate)).status_code == 200
    assert auth(other_student).get(_url("certificate", certificate)).status_code == 404


# ---------------------------------------------------------------------------
# Where the bytes live, and what they are called
# ---------------------------------------------------------------------------
def test_the_stored_name_is_not_the_name_that_was_uploaded(portfolio):
    stored = portfolio.file.name

    assert "resume" not in stored, f"the uploader's filename survived: {stored}"
    assert stored.startswith("portfolio/items/")
    assert stored.endswith(".pdf")
    # 32 hex characters — a uuid4 with the dashes removed.
    assert len(stored.rsplit("/", 1)[-1]) == 36


def test_the_file_is_not_written_into_the_public_media_root(portfolio):
    from pathlib import Path

    public = Path(settings.MEDIA_ROOT) / portfolio.file.name
    private = Path(settings.PRIVATE_MEDIA_ROOT) / portfolio.file.name

    assert not public.exists(), "a private upload landed in the published directory"
    assert private.exists()


def test_private_storage_refuses_to_invent_a_public_url(portfolio):
    """`file.url` must fail loudly rather than hand back a dead public link."""
    with pytest.raises(Exception):
        portfolio.file.url


# ---------------------------------------------------------------------------
# The API hands out the protected address, not a media path
# ---------------------------------------------------------------------------
def test_the_portfolio_api_returns_a_protected_link(auth, student, portfolio):
    response = auth(student).get("/api/v1/cv/portfolio/")

    assert response.status_code == 200, response.data
    row = response.data["results"][0]
    assert "/api/v1/files/portfolio-item/" in row["file_url"]
    assert "/media/" not in str(row)


def test_tests_never_write_into_the_projects_own_upload_directories():
    """Test uploads belong to a scratch directory that is thrown away."""
    from pathlib import Path

    from apps.common.storage import private_storage

    project = Path(settings.BASE_DIR).resolve()
    for root in (settings.MEDIA_ROOT, settings.PRIVATE_MEDIA_ROOT, private_storage.location):
        assert project not in Path(root).resolve().parents, root
