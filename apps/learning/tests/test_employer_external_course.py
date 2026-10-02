"""An employer lists a Coursera course.

The address is not free text — the learner is sent there from a page with the
platform's name on it — so only the platforms in settings are accepted, and
what is stored is rebuilt without whatever query string came with it. The
course then goes through the same review as any other, needing skills instead
of lessons.
"""

import pytest

from apps.common.enums import ModerationStatus

pytestmark = pytest.mark.django_db

COURSES_URL = "/api/v1/learning/courses/"
COURSERA = "https://www.coursera.org/learn/sql-for-data-science"


@pytest.fixture
def category(db):
    from apps.taxonomy.models import SkillCategory

    return SkillCategory.objects.get_or_create(slug="data", defaults={"name_uz": "Data"})[0]


@pytest.fixture
def skill(db, category):
    from apps.taxonomy.models import Skill

    return Skill.objects.get_or_create(
        slug="sql", defaults={"name_uz": "SQL", "category": category}
    )[0]


def create(client, category, url):
    return client.post(
        COURSES_URL,
        {"title": "SQL on Coursera", "category": str(category.id), "external_url": url},
        format="json",
    )


def test_an_employer_can_list_a_coursera_course(auth, employer, category):
    response = create(auth(employer), category, COURSERA)

    assert response.status_code == 201, response.data
    from apps.learning.models import Course

    course = Course.objects.get(id=response.data["id"])
    assert course.external_url == COURSERA
    assert course.employer_id == employer.employer_profile.id


def test_the_query_string_is_dropped(auth, employer, category):
    """An affiliate tag pasted with the link must not follow every learner."""
    response = create(
        auth(employer), category, f"{COURSERA}/?utm_source=x&ref=employer#reviews"
    )

    assert response.status_code == 201, response.data
    assert response.data["external_url"] == COURSERA


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/learn/sql",
        "https://coursera.org.evil.example.com/learn/sql",
        "javascript:alert(1)",
        "https://www.coursera.org/",
        "not a url",
    ],
)
def test_other_addresses_are_refused(auth, employer, category, url):
    response = create(auth(employer), category, url)

    assert response.status_code == 400, url


def test_a_learner_sees_coursera_and_the_company(
    auth, employer, student, category, skill
):
    from apps.learning.models import Course

    response = create(auth(employer), category, COURSERA)
    course = Course.objects.get(id=response.data["id"])
    course.status = ModerationStatus.PUBLISHED
    course.save()

    row = next(
        item
        for item in auth(student).get(f"{COURSES_URL}?page_size=100").data["results"]
        if item["id"] == str(course.id)
    )
    assert row["external_platform"] == "Coursera"
    assert row["provider_name"] == employer.employer_profile.display_name


# -- review ------------------------------------------------------------------
def test_it_can_be_submitted_without_lessons_once_it_has_a_skill(
    auth, employer, category, skill
):
    client = auth(employer)
    course_id = create(client, category, COURSERA).data["id"]

    refused = client.post(f"{COURSES_URL}{course_id}/submit/")
    assert refused.status_code == 400
    assert refused.data["error"]["code"] == "course_no_skills"

    client.post(f"{COURSES_URL}{course_id}/skills/", {"skill": str(skill.id)}, format="json")
    accepted = client.post(f"{COURSES_URL}{course_id}/submit/")

    assert accepted.status_code == 200, accepted.data
    assert accepted.data["status"] == ModerationStatus.PENDING_REVIEW


def test_an_ordinary_course_still_needs_lessons(auth, employer, category, skill):
    client = auth(employer)
    course_id = client.post(
        COURSES_URL, {"title": "Here", "category": str(category.id)}, format="json"
    ).data["id"]
    client.post(f"{COURSES_URL}{course_id}/skills/", {"skill": str(skill.id)}, format="json")

    response = client.post(f"{COURSES_URL}{course_id}/submit/")

    assert response.status_code == 400
    assert response.data["error"]["code"] == "course_empty"


# -- taken here or elsewhere, not both ---------------------------------------
def test_an_external_course_takes_no_modules(auth, employer, category):
    client = auth(employer)
    course_id = create(client, category, COURSERA).data["id"]

    response = client.post(
        "/api/v1/learning/modules/", {"course": course_id, "title": "Week 1"}, format="json"
    )

    assert response.status_code == 400


def test_a_course_with_lessons_cannot_become_external(auth, employer, category):
    from apps.learning.models import CourseModule

    client = auth(employer)
    course_id = client.post(
        COURSES_URL, {"title": "Here", "category": str(category.id)}, format="json"
    ).data["id"]
    CourseModule.objects.create(course_id=course_id, title="Week 1")

    response = client.patch(
        f"{COURSES_URL}{course_id}/", {"external_url": COURSERA}, format="json"
    )

    assert response.status_code == 400


def test_clearing_the_link_makes_it_an_ordinary_course(auth, employer, category):
    client = auth(employer)
    course_id = create(client, category, COURSERA).data["id"]

    response = client.patch(f"{COURSES_URL}{course_id}/", {"external_url": ""}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["external_url"] == ""


# -- skills ------------------------------------------------------------------
def test_a_skill_can_be_taken_off_again(auth, employer, category, skill):
    from apps.learning.models import CourseSkill

    client = auth(employer)
    course_id = create(client, category, COURSERA).data["id"]
    client.post(f"{COURSES_URL}{course_id}/skills/", {"skill": str(skill.id)}, format="json")

    response = client.delete(f"{COURSES_URL}{course_id}/skills/{skill.id}/")

    assert response.status_code == 204
    assert not CourseSkill.objects.filter(course_id=course_id).exists()


def test_a_rival_cannot_take_skills_off(auth, employer, other_employer, category, skill):
    from apps.learning.models import CourseSkill

    course_id = create(auth(employer), category, COURSERA).data["id"]
    auth(employer).post(
        f"{COURSES_URL}{course_id}/skills/", {"skill": str(skill.id)}, format="json"
    )

    response = auth(other_employer).delete(f"{COURSES_URL}{course_id}/skills/{skill.id}/")

    assert response.status_code in {403, 404}
    assert CourseSkill.objects.filter(course_id=course_id).exists()
