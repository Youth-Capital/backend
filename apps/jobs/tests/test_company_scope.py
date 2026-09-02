"""A company sees its own vacancies, and only its own candidates.

The jobs app had no tests at all, which is how the following survived: the
vacancy list answered an employer with "every published vacancy in the country,
plus mine". The employer's own vacancy page therefore listed rival companies'
postings, and clicking one opened its candidate list — where the ownership
check correctly refused with 403.

Nothing was ever leaked. The damage was that the product invited a click it was
going to refuse, and the page rendered that refusal as an empty list, so it
read as "you have no candidates" instead of "this is not yours".

So there are two separate guarantees here, and both need holding:

* the **list** must not offer another company's rows;
* the **ownership check** must refuse them even if something does.

The second is the security boundary and was already sound. The first is the one
that broke. Testing only the boundary would have let this bug through again,
which is the point of the first block below.
"""

import pytest

from apps.common.enums import ModerationStatus, RequirementLevel

pytestmark = pytest.mark.django_db

VACANCIES_URL = "/api/v1/jobs/vacancies/"


@pytest.fixture
def rival_vacancy(db, other_employer, taxonomy):
    """A published vacancy belonging to somebody else."""
    from apps.jobs.models import Vacancy, VacancySkill

    rival = Vacancy.objects.create(
        employer=other_employer.employer_profile,
        title="Rival Analyst",
        description="Belongs to RivalCo",
        region=taxonomy["region"],
        min_experience_months=0,
        status=ModerationStatus.PUBLISHED,
    )
    VacancySkill.objects.create(
        vacancy=rival,
        skill=taxonomy["sql"],
        requirement=RequirementLevel.REQUIRED,
        min_knowledge_score=60,
    )
    return rival


def titles(response):
    return {row["title"] for row in response.data["results"]}


# -- the list -------------------------------------------------------------
def test_an_employer_sees_only_their_own_vacancies(
    auth, employer, vacancy, rival_vacancy
):
    """The regression this file was written for."""
    response = auth(employer).get(f"{VACANCIES_URL}?page_size=100")

    assert response.status_code == 200
    assert titles(response) == {vacancy.title}, (
        "the employer's list offered another company's vacancy — which is how "
        "a 403 candidate page became reachable by clicking"
    )


def test_an_employer_still_sees_their_own_unpublished_vacancy(
    auth, employer, taxonomy
):
    """Scoping to the company must not hide drafts: this is a management view."""
    from apps.jobs.models import Vacancy

    draft = Vacancy.objects.create(
        employer=employer.employer_profile,
        title="Draft role",
        description="Not published yet",
        region=taxonomy["region"],
        min_experience_months=0,
        status=ModerationStatus.DRAFT,
    )

    response = auth(employer).get(f"{VACANCIES_URL}?page_size=100")

    assert draft.title in titles(response)


def test_the_public_board_is_an_explicit_opt_in(
    auth, employer, vacancy, rival_vacancy
):
    """Browsing everyone's postings stays possible, but never by default."""
    response = auth(employer).get(f"{VACANCIES_URL}?scope=board&page_size=100")

    assert titles(response) == {vacancy.title, rival_vacancy.title}


def test_a_student_still_sees_the_whole_published_board(
    auth, student, vacancy, rival_vacancy
):
    """The same endpoint serves the job board; scoping employers must not touch it."""
    response = auth(student).get(f"{VACANCIES_URL}?page_size=100")

    assert titles(response) == {vacancy.title, rival_vacancy.title}


def test_an_admin_still_sees_everything(auth, admin_user, vacancy, rival_vacancy):
    response = auth(admin_user).get(f"{VACANCIES_URL}?page_size=100")

    assert {vacancy.title, rival_vacancy.title} <= titles(response)


def test_an_employer_without_a_company_sees_nothing_rather_than_everything(
    auth, db, vacancy, rival_vacancy
):
    """The safe default when there is no company to scope to."""
    from apps.accounts.models import User
    from apps.common.enums import Role

    orphan = User.objects.create_user(
        email="nocompany@test.uz", password="TestPass12345", role=Role.EMPLOYER
    )

    response = auth(orphan).get(f"{VACANCIES_URL}?page_size=100")

    assert response.data["results"] == []


# -- the boundary ---------------------------------------------------------
def test_another_companys_candidate_list_is_not_found(auth, other_employer, vacancy):
    """Typing the URL by hand does not confirm the vacancy exists.

    404 rather than 403 is the stronger answer, and it is what scoping the
    queryset buys: `get_object` looks in the caller's own rows, so a foreign id
    is simply absent. A 403 would have told a rival that this id is real.
    """
    response = auth(other_employer).get(f"{VACANCIES_URL}{vacancy.id}/candidates/")

    assert response.status_code == 404


def test_the_ownership_check_still_refuses_a_resolvable_foreign_vacancy(
    auth, other_employer, vacancy
):
    """Two layers, tested separately.

    With `scope=board` the object *does* resolve — an employer is allowed to
    read a published listing. The candidate list behind it must still be
    refused, and by the ownership check rather than by the queryset. If this
    ever passes, hiding rows would be the only thing protecting the data,
    which is not access control.
    """
    response = auth(other_employer).get(
        f"{VACANCIES_URL}{vacancy.id}/candidates/?scope=board"
    )

    assert response.status_code == 403


def test_another_companys_vacancy_cannot_be_edited(auth, other_employer, vacancy):
    response = auth(other_employer).patch(
        f"{VACANCIES_URL}{vacancy.id}/", {"title": "Hijacked"}, format="json"
    )

    assert response.status_code in {403, 404}
    vacancy.refresh_from_db()
    assert vacancy.title != "Hijacked"


def test_the_owner_can_still_read_their_own_candidate_list(auth, employer, vacancy):
    """Scoping must not lock the owner out of their own vacancy."""
    response = auth(employer).get(f"{VACANCIES_URL}{vacancy.id}/candidates/")

    assert response.status_code == 200
    assert "results" in response.data
