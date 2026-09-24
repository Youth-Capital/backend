"""A placement is a claim that somebody was hired. Only the hirer may make it.

Placements are the number the programme is judged on — how many young people
actually got work. The endpoint that writes them was a plain `ModelViewSet`
behind `IsAuthenticated`, with `student` and `employer` writable in the
serialiser and no `perform_create` at all. Any signed-in account could post a
hiring record naming any student and any company, and delete its own again
afterwards.

So the rules these pin are: who may write one (the hiring company, or an
admin), and where the names in it come from (the application, never the request
body).
"""

import datetime

import pytest

from apps.jobs.models import Application, Placement, PlacementStatus

pytestmark = pytest.mark.django_db

URL = "/api/v1/jobs/placements/"


@pytest.fixture
def application(db, student, vacancy):
    """The student applied to the employer's vacancy, and was hired."""
    return Application.objects.create(student=student, vacancy=vacancy)


@pytest.fixture
def placement(db, application, employer, student, vacancy):
    return Placement.objects.create(
        student=student,
        employer=employer.employer_profile,
        vacancy=vacancy,
        application=application,
        position="Junior Analyst",
        start_date=datetime.date(2026, 1, 15),
        status=PlacementStatus.ACTIVE,
    )


def _payload(**overrides):
    body = {"position": "Invented role", "start_date": "2026-02-01"}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Vertical escalation: a learner is not a hirer
# ---------------------------------------------------------------------------
def test_a_student_cannot_invent_their_own_hiring(auth, student, employer, application):
    response = auth(student).post(
        URL,
        _payload(
            student=str(student.id),
            employer=str(employer.employer_profile.id),
            application=str(application.id),
        ),
        format="json",
    )

    assert response.status_code == 403, response.data
    assert not Placement.objects.filter(position="Invented role").exists()


def test_a_student_cannot_invent_a_hiring_for_somebody_else(
    auth, student, other_student, employer, application
):
    """Horizontal escalation: writing a record onto another person's account."""
    response = auth(student).post(
        URL,
        _payload(
            student=str(other_student.id),
            employer=str(employer.employer_profile.id),
            application=str(application.id),
        ),
        format="json",
    )

    assert response.status_code == 403, response.data
    assert not Placement.objects.filter(student=other_student).exists()


def test_a_student_cannot_delete_their_placement(auth, student, placement):
    """Deleting is tampering too: the KPI moves either way."""
    response = auth(student).delete(f"{URL}{placement.id}/")

    assert response.status_code in {403, 404}, response.data
    assert Placement.objects.filter(id=placement.id).exists()


def test_a_student_cannot_edit_their_placement(auth, student, placement):
    response = auth(student).patch(
        f"{URL}{placement.id}/", {"position": "Chief Executive"}, format="json"
    )

    assert response.status_code in {403, 404}, response.data
    placement.refresh_from_db()
    assert placement.position == "Junior Analyst"


# ---------------------------------------------------------------------------
# Horizontal escalation between companies
# ---------------------------------------------------------------------------
def test_an_employer_cannot_record_a_hiring_against_another_company(
    auth, other_employer, application, employer
):
    """The rival owns neither the vacancy nor the application behind it."""
    response = auth(other_employer).post(
        URL, _payload(application=str(application.id)), format="json"
    )

    assert response.status_code in {403, 404}, response.data
    assert not Placement.objects.filter(position="Invented role").exists()


def test_the_company_in_the_body_is_ignored(auth, employer, other_employer, application):
    """Whose name goes on the record is decided by the server.

    Even for a caller entitled to create *a* placement, the company is taken
    from the application, not from what they typed.
    """
    response = auth(employer).post(
        URL,
        _payload(
            application=str(application.id),
            employer=str(other_employer.employer_profile.id),
        ),
        format="json",
    )

    assert response.status_code == 201, response.data
    created = Placement.objects.get(id=response.data["id"])
    assert created.employer_id == employer.employer_profile.id


def test_the_student_in_the_body_is_ignored(
    auth, employer, student, other_student, application
):
    response = auth(employer).post(
        URL,
        _payload(application=str(application.id), student=str(other_student.id)),
        format="json",
    )

    assert response.status_code == 201, response.data
    created = Placement.objects.get(id=response.data["id"])
    assert created.student_id == student.id, "the body renamed who was hired"


def test_a_rival_cannot_edit_another_companys_placement(auth, other_employer, placement):
    response = auth(other_employer).patch(
        f"{URL}{placement.id}/", {"position": "Rewritten"}, format="json"
    )

    assert response.status_code in {403, 404}, response.data
    placement.refresh_from_db()
    assert placement.position == "Junior Analyst"


def test_a_rival_cannot_delete_another_companys_placement(auth, other_employer, placement):
    response = auth(other_employer).delete(f"{URL}{placement.id}/")

    assert response.status_code in {403, 404}, response.data
    assert Placement.objects.filter(id=placement.id).exists()


# ---------------------------------------------------------------------------
# A placement must rest on something that happened
# ---------------------------------------------------------------------------
def test_a_placement_cannot_be_conjured_without_an_application(auth, employer):
    response = auth(employer).post(URL, _payload(), format="json")

    assert response.status_code == 400, response.data
    assert not Placement.objects.filter(position="Invented role").exists()


# ---------------------------------------------------------------------------
# The hirer's own workflow still works
# ---------------------------------------------------------------------------
def test_the_hiring_company_records_a_placement(auth, employer, student, application):
    response = auth(employer).post(
        URL, _payload(application=str(application.id)), format="json"
    )

    assert response.status_code == 201, response.data
    created = Placement.objects.get(id=response.data["id"])
    assert created.student_id == student.id
    assert created.employer_id == employer.employer_profile.id
    assert created.vacancy_id == application.vacancy_id


def test_the_hiring_company_can_edit_its_own_placement(auth, employer, placement):
    response = auth(employer).patch(
        f"{URL}{placement.id}/", {"position": "Analyst II"}, format="json"
    )

    assert response.status_code == 200, response.data
    placement.refresh_from_db()
    assert placement.position == "Analyst II"


def test_an_admin_may_correct_the_record(auth, admin_user, placement):
    response = auth(admin_user).patch(
        f"{URL}{placement.id}/", {"position": "Corrected"}, format="json"
    )

    assert response.status_code == 200, response.data


# ---------------------------------------------------------------------------
# Reading stays scoped
# ---------------------------------------------------------------------------
def test_a_student_sees_only_their_own_placements(auth, other_student, placement):
    response = auth(other_student).get(URL)

    assert response.status_code == 200
    assert response.data["count"] == 0


def test_a_rival_company_sees_no_placements_of_ours(auth, other_employer, placement):
    response = auth(other_employer).get(URL)

    assert response.status_code == 200
    assert response.data["count"] == 0
