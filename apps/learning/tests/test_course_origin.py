"""An admin sees who put a course on the platform; nobody else does.

The moderation screen showed a course title and a company name, and nothing
that let the moderator reach the person who wrote it. `origin` carries the
company and the author — email included — and is therefore admin-only: the
same serializer is the learner's catalogue.
"""

import pytest

from apps.common.enums import ModerationStatus

pytestmark = pytest.mark.django_db

COURSES_URL = "/api/v1/learning/courses/"


@pytest.fixture
def course(db, employer):
    from apps.learning.models import Course

    return Course.objects.create(
        title="Origin course", slug="origin-course", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )


@pytest.fixture
def draft(db, other_employer):
    from apps.learning.models import Course

    return Course.objects.create(
        title="Rival draft", slug="rival-draft", author=other_employer,
        employer=other_employer.employer_profile, status=ModerationStatus.DRAFT,
    )


def row(response, title):
    return next(item for item in response.data["results"] if item["title"] == title)


def test_an_admin_sees_the_company_and_the_author(auth, admin_user, employer, course):
    response = auth(admin_user).get(f"{COURSES_URL}?page_size=100")

    origin = row(response, course.title)["origin"]
    assert origin["company"]["id"] == str(employer.employer_profile.id)
    assert origin["author"]["email"] == employer.email


def test_the_detail_carries_it_too(auth, admin_user, employer, course):
    response = auth(admin_user).get(f"{COURSES_URL}{course.id}/")

    assert response.data["origin"]["author"]["email"] == employer.email


def test_an_admin_sees_other_companies_drafts_with_their_origin(
    auth, admin_user, other_employer, draft
):
    response = auth(admin_user).get(f"{COURSES_URL}?page_size=100")

    assert row(response, draft.title)["origin"]["author"]["email"] == other_employer.email


def test_a_student_gets_no_origin(auth, student, course):
    response = auth(student).get(f"{COURSES_URL}?page_size=100")

    assert row(response, course.title)["origin"] is None


def test_the_owning_employer_gets_no_origin_either(auth, employer, course):
    """Not a secret from them — but the field is an admin tool, not theirs."""
    response = auth(employer).get(f"{COURSES_URL}{course.id}/")

    assert response.data["origin"] is None


def test_an_admin_can_find_a_course_by_its_authors_email(
    auth, admin_user, employer, course
):
    response = auth(admin_user).get(f"{COURSES_URL}?search={employer.email}")

    assert course.title in {item["title"] for item in response.data["results"]}


def test_nobody_else_can_search_by_an_authors_email(auth, student, employer, course):
    """Otherwise the catalogue answers "did this address write a course?"."""
    response = auth(student).get(f"{COURSES_URL}?search={employer.email}")

    assert course.title not in {item["title"] for item in response.data["results"]}


def test_an_admin_can_filter_by_company(auth, admin_user, employer, course, draft):
    response = auth(admin_user).get(
        f"{COURSES_URL}?employer={employer.employer_profile.id}&page_size=100"
    )

    titles = {item["title"] for item in response.data["results"]}
    assert course.title in titles
    assert draft.title not in titles
