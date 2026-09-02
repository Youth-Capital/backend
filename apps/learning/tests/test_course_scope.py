"""A company's course page shows that company's courses.

The employer course list answered "every published course on the platform,
plus mine". One employer's management page therefore listed six other
companies' courses beside their own — each with a Analytics button that
answers 403 when pressed.

Nothing leaked: analytics and modules were both refused. The damage is the
same as the vacancy list had — the product invites a click it is going to
refuse, and the page has no way to explain why.

Both halves are pinned below, separately: what the list offers, and what the
per-course endpoints refuse. Testing only the refusal would let the list break
again unnoticed.
"""

import pytest

from apps.common.enums import ModerationStatus

pytestmark = pytest.mark.django_db

COURSES_URL = "/api/v1/learning/courses/"


@pytest.fixture
def my_course(db, employer):
    from apps.learning.models import Course

    return Course.objects.create(
        title="Mine", slug="mine", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )


@pytest.fixture
def my_draft(db, employer):
    from apps.learning.models import Course

    return Course.objects.create(
        title="My draft", slug="my-draft", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.DRAFT,
    )


@pytest.fixture
def rival_course(db, other_employer):
    from apps.learning.models import Course

    return Course.objects.create(
        title="Theirs", slug="theirs", author=other_employer,
        employer=other_employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )


@pytest.fixture
def platform_course(db, admin_user):
    from apps.learning.models import Course

    return Course.objects.create(
        title="Platform course", slug="platform-course", author=admin_user,
        status=ModerationStatus.PUBLISHED, provider_type="PLATFORM",
    )


def titles(response):
    return {row["title"] for row in response.data["results"]}


# -- the list --------------------------------------------------------------
def test_an_employer_sees_only_their_own_courses(
    auth, employer, my_course, my_draft, rival_course, platform_course
):
    """The regression this file was written for."""
    response = auth(employer).get(f"{COURSES_URL}?page_size=100")

    assert response.status_code == 200
    assert titles(response) == {my_course.title, my_draft.title}, (
        "the management page offered courses the company does not own"
    )


def test_drafts_are_still_there(auth, employer, my_course, my_draft):
    """Scoping to the company must not hide unfinished work."""
    response = auth(employer).get(f"{COURSES_URL}?page_size=100")

    assert my_draft.title in titles(response)


def test_the_catalogue_is_an_explicit_opt_in(
    auth, employer, my_course, rival_course, platform_course
):
    response = auth(employer).get(f"{COURSES_URL}?scope=catalogue&page_size=100")

    assert {my_course.title, rival_course.title, platform_course.title} <= titles(
        response
    )


def test_a_student_still_sees_the_whole_catalogue(
    auth, student, my_course, rival_course, platform_course
):
    """The same endpoint is the learner's catalogue; scoping must not touch it."""
    response = auth(student).get(f"{COURSES_URL}?page_size=100")

    assert {my_course.title, rival_course.title, platform_course.title} <= titles(
        response
    )


def test_a_student_never_sees_a_draft(auth, student, my_draft):
    response = auth(student).get(f"{COURSES_URL}?page_size=100")

    assert my_draft.title not in titles(response)


def test_an_admin_still_sees_everything(
    auth, admin_user, my_course, my_draft, rival_course
):
    response = auth(admin_user).get(f"{COURSES_URL}?page_size=100")

    assert {my_course.title, my_draft.title, rival_course.title} <= titles(response)


# -- the boundary ----------------------------------------------------------
def test_another_companys_analytics_is_refused(auth, employer, rival_course):
    """Held independently of what the list offers."""
    response = auth(employer).get(f"{COURSES_URL}{rival_course.id}/analytics/")

    assert response.status_code in {403, 404}


def test_another_companys_course_cannot_be_edited(auth, employer, rival_course):
    response = auth(employer).patch(
        f"{COURSES_URL}{rival_course.id}/", {"title": "Hijacked"}, format="json"
    )

    assert response.status_code in {403, 404}
    rival_course.refresh_from_db()
    assert rival_course.title == "Theirs"


def test_the_owner_can_still_read_their_own_analytics(auth, employer, my_course):
    """Closing the hole must not close the door."""
    response = auth(employer).get(f"{COURSES_URL}{my_course.id}/analytics/")

    assert response.status_code == 200, response.data


# -- reading the inside of your own course ---------------------------------
def test_the_owning_company_can_read_a_course_it_did_not_author(
    auth, employer, admin_user, db
):
    """Editing was allowed, reading was not — an inconsistency with teeth.

    `can_open_course` accepted the author and enrolled learners, while
    `_assert_can_edit` also accepted the owning company. So a course the
    company owns but did not type — one an admin set up for them, or one whose
    author changed — could be edited by that company and not read by it. The
    employer opened their own course to check it and saw a locked list.

    Authored by the admin, owned by the company, opened by the company: the
    exact case, and it would fail without the company branch.
    """
    from apps.learning.models import Course, CourseModule, Lesson

    course = Course.objects.create(
        title="Set up for them", slug="set-up-for-them", author=admin_user,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )
    module = CourseModule.objects.create(course=course, title="M", order=0)
    lesson = Lesson.objects.create(
        module=module, title="L", content="Body", order=0, is_free_preview=False
    )

    response = auth(employer).get(f"/api/v1/learning/lessons/{lesson.id}/")

    assert response.status_code == 200, response.data
    assert response.data["content"] == "Body"


def test_a_rival_still_cannot_read_a_locked_lesson(auth, other_employer, my_course, db):
    from apps.learning.models import CourseModule, Lesson

    module = CourseModule.objects.create(course=my_course, title="M", order=0)
    lesson = Lesson.objects.create(
        module=module, title="L", content="Body", order=0, is_free_preview=False
    )

    response = auth(other_employer).get(f"/api/v1/learning/lessons/{lesson.id}/")

    assert response.status_code in {403, 404}


def test_a_lesson_list_says_which_lessons_have_video(auth, employer, my_course, db):
    """What an author comes to this page to check."""
    from apps.learning.models import CourseModule, Lesson

    module = CourseModule.objects.create(course=my_course, title="M", order=0)
    Lesson.objects.create(module=module, title="With", order=0,
                          video_url="https://youtu.be/dQw4w9WgXcQ")
    Lesson.objects.create(module=module, title="Without", order=1)

    response = auth(employer).get(f"{COURSES_URL}{my_course.id}/")

    lessons = {row["title"]: row["has_video"] for row in response.data["modules"][0]["lessons"]}
    assert lessons == {"With": True, "Without": False}
