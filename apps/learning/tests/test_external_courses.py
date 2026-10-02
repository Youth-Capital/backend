"""Courses taken on a partner's site — Coursera, for now.

Such a course is a catalogue entry and a link: no lessons here, no enrolment
here, and skills attached so the career page and the plan recommend it.
"""

import json
from pathlib import Path

import pytest

from apps.common.enums import ModerationStatus
from apps.learning.importers import import_catalogue
from apps.learning.models import Course, Enrollment
from apps.learning.providers import ContentProvider, ImportStatus

pytestmark = pytest.mark.django_db

COURSES_URL = "/api/v1/learning/courses/"
URL = "https://www.coursera.org/learn/sql-for-data-science"


@pytest.fixture
def provider():
    return ContentProvider.objects.create(name="Coursera", slug="coursera")


@pytest.fixture
def sql_skill(db):
    from apps.taxonomy.models import Skill, SkillCategory

    category, _ = SkillCategory.objects.get_or_create(slug="data", defaults={"name_uz": "Data"})
    return Skill.objects.get_or_create(slug="sql", defaults={"name_uz": "SQL", "category": category})[0]


def entry(**overrides):
    base = {
        "external_id": "sql-for-data-science",
        "title": "SQL for Data Science",
        "language": "en",
        "level": "BEGINNER",
        "external_url": URL,
        "skills": ["sql", "no-such-skill"],
    }
    base.update(overrides)
    return {"courses": [base]}


def test_an_external_course_imports_without_modules(provider, sql_skill):
    run = import_catalogue(provider, entry())

    assert run.status == ImportStatus.SUCCEEDED, run.errors
    course = Course.objects.get(provider=provider)
    assert course.external_url == URL
    assert course.modules.count() == 0
    assert course.provider_type == "PARTNER"


def test_known_skills_are_linked_and_unknown_ones_ignored(provider, sql_skill):
    import_catalogue(provider, entry())

    course = Course.objects.get(provider=provider)
    assert [link.skill.slug for link in course.skill_links.all()] == ["sql"]


def test_it_waits_for_review_unless_published_deliberately(provider, sql_skill):
    import_catalogue(provider, entry())
    assert Course.objects.get(provider=provider).status == ModerationStatus.PENDING_REVIEW

    import_catalogue(provider, entry(), publish=True)
    course = Course.objects.get(provider=provider)
    assert course.status == ModerationStatus.PUBLISHED
    assert course.published_at is not None


def test_reimport_updates_rather_than_duplicates(provider, sql_skill):
    import_catalogue(provider, entry())
    import_catalogue(provider, entry(title="SQL for Data Science (2nd ed.)"))

    assert Course.objects.filter(provider=provider).count() == 1
    assert Course.objects.get(provider=provider).title.endswith("(2nd ed.)")


def test_an_http_link_is_refused(provider):
    run = import_catalogue(provider, entry(external_url="http://example.com/course"))

    assert run.status == ImportStatus.FAILED
    assert "https" in run.errors[0]["error"]


def test_an_external_course_with_lessons_is_refused(provider):
    run = import_catalogue(
        provider,
        entry(modules=[{"title": "M", "lessons": [{"title": "L"}]}]),
    )

    assert run.status == ImportStatus.FAILED


def test_a_learner_sees_the_link_and_the_partner_name(auth, student, provider, sql_skill):
    import_catalogue(provider, entry(), publish=True)

    response = auth(student).get(f"{COURSES_URL}?page_size=100")

    row = next(r for r in response.data["results"] if r["title"] == "SQL for Data Science")
    assert row["external_url"] == URL
    assert row["provider_name"] == "Coursera"


def test_an_external_course_cannot_be_enrolled_on(auth, student, provider, sql_skill):
    import_catalogue(provider, entry(), publish=True)
    course = Course.objects.get(provider=provider)

    response = auth(student).post(f"{COURSES_URL}{course.id}/enroll/")

    assert response.status_code == 400, response.data
    assert not Enrollment.objects.filter(course=course).exists()


def test_the_shipped_coursera_catalogue_is_valid():
    """The file in the repo must import cleanly — every entry, not most."""
    path = Path(__file__).resolve().parents[1] / "catalogues" / "coursera.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    provider = ContentProvider.objects.create(name="Coursera", slug="coursera")
    run = import_catalogue(provider, payload)

    assert run.errors == []
    assert run.courses_created == len(payload["courses"])
    for course in Course.objects.filter(provider=provider):
        assert course.external_url.startswith("https://www.coursera.org/")


# ---------------------------------------------------------------------------
# One rule for the address, whichever door the course comes through
# ---------------------------------------------------------------------------
# The importer used to check only that the address began with "https://", so
# it accepted everything the authoring API refuses: any host at all, a
# lookalike domain, credentials in front of the real host, a tracking query.
# A partner file is run by an administrator and lands in the moderation queue,
# but with --publish it reaches learners unreviewed — and the page it is shown
# on carries the platform's name. Both paths now call clean_external_url.
REFUSED = [
    "https://evil.example/free-course",
    "https://user:pass@evil.example/c",
    "https://coursera.org.evil.example/c",
    "https://www.coursera.org.evil.example/learn/sql",
    "https://notcoursera.org/learn/sql",
    "https://www.coursera.org",  # the home page is not a course
    "javascript:alert(1)",
    "//www.coursera.org/learn/sql",  # no scheme
]


@pytest.mark.parametrize("address", REFUSED)
def test_the_importer_refuses_what_the_api_refuses(provider, sql_skill, address):
    run = import_catalogue(provider, entry(external_url=address))

    assert run.status == ImportStatus.FAILED, address
    assert not Course.objects.filter(provider=provider).exists()
    assert "EXTERNAL_COURSE_PLATFORMS" in run.errors[0]["error"]


@pytest.mark.parametrize("address", REFUSED)
def test_the_two_paths_agree_on_every_refusal(address):
    """The property, not the message: one function decides for both."""
    from apps.learning.external import clean_external_url

    with pytest.raises(ValueError):
        clean_external_url(address)


def test_an_allowed_course_page_still_imports(provider, sql_skill):
    run = import_catalogue(provider, entry(external_url=URL))

    assert run.status == ImportStatus.SUCCEEDED, run.errors
    assert Course.objects.get(provider=provider).external_url == URL


def test_a_tracking_query_is_dropped_on_the_way_in(provider, sql_skill):
    """Validated and then stored raw was the other half of the problem: the
    address every learner clicks carried whatever came with the file."""
    run = import_catalogue(
        provider,
        entry(external_url=f"{URL}?utm_source=partner&aff=123#week-2"),
    )

    assert run.status == ImportStatus.SUCCEEDED, run.errors
    assert Course.objects.get(provider=provider).external_url == URL


def test_a_plain_http_address_is_stored_as_https(provider, sql_skill):
    """Changed behaviour, pinned on purpose.

    The old prefix check refused http outright; the shared rule upgrades it,
    exactly as it does for an employer typing one into the form. Either way no
    learner is sent over plain http — the stored address is https.
    """
    run = import_catalogue(
        provider, entry(external_url="http://www.coursera.org/learn/sql-for-data-science")
    )

    assert run.status == ImportStatus.SUCCEEDED, run.errors
    assert Course.objects.get(provider=provider).external_url.startswith("https://")


def test_a_www_prefixed_host_is_kept_as_written(provider, sql_skill):
    """The allowlist is matched without "www.", and the host is stored as given."""
    run = import_catalogue(provider, entry(external_url="https://coursera.org/learn/sql"))

    assert run.status == ImportStatus.SUCCEEDED, run.errors
    assert Course.objects.get(provider=provider).external_url == "https://coursera.org/learn/sql"


def test_an_address_too_long_for_the_column_is_refused(provider, sql_skill):
    """Refused rather than truncated: a cut URL points somewhere else."""
    run = import_catalogue(
        provider, entry(external_url=f"https://www.coursera.org/learn/{'x' * 520}")
    )

    assert run.status == ImportStatus.FAILED
    assert "500 characters" in run.errors[0]["error"]
