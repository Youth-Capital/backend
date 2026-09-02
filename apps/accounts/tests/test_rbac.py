"""Role and ownership enforcement.

Prompt §29: "Не полагаться только на скрытие кнопок на frontend."
Every case here would pass a UI-only check and must fail on the server.
"""

import pytest

pytestmark = pytest.mark.django_db


def test_anonymous_cannot_read_protected_endpoints(api):
    for url in ("/api/v1/me/profile/", "/api/v1/jobs/vacancies/", "/api/v1/taxonomy/skills/"):
        assert api.get(url).status_code == 401, url


def test_student_cannot_open_admin_analytics(auth, student):
    client = auth(student)
    assert client.get("/api/v1/analytics/dashboard/").status_code == 403


def test_employer_cannot_open_admin_analytics(auth, employer):
    client = auth(employer)
    assert client.get("/api/v1/analytics/dashboard/").status_code == 403


def test_admin_can_open_admin_analytics(auth, admin_user):
    client = auth(admin_user)
    assert client.get("/api/v1/analytics/dashboard/").status_code == 200


def test_student_cannot_open_employer_dashboard(auth, student):
    client = auth(student)
    assert client.get("/api/v1/jobs/employer/dashboard/").status_code == 403


def test_employer_cannot_read_audit_log(auth, employer):
    client = auth(employer)
    assert client.get("/api/v1/audit/logs/").status_code == 403


def test_employer_cannot_see_another_companys_applications(
    auth, employer, other_employer, student, vacancy
):
    from apps.jobs.services import apply_to_vacancy

    apply_to_vacancy(student=student, vacancy=vacancy, cover_letter="hi")

    owner_view = auth(employer).get("/api/v1/jobs/applications/")
    assert owner_view.status_code == 200
    assert owner_view.json()["count"] == 1

    rival_view = auth(other_employer).get("/api/v1/jobs/applications/")
    assert rival_view.status_code == 200
    assert rival_view.json()["count"] == 0


def test_student_sees_only_their_own_applications(
    auth, student, other_student, vacancy
):
    from apps.jobs.services import apply_to_vacancy

    apply_to_vacancy(student=student, vacancy=vacancy)

    response = auth(other_student).get("/api/v1/jobs/applications/")
    assert response.json()["count"] == 0


def test_student_cannot_change_application_status(auth, student, vacancy):
    from apps.jobs.services import apply_to_vacancy

    application = apply_to_vacancy(student=student, vacancy=vacancy)

    response = auth(student).post(
        f"/api/v1/jobs/applications/{application.id}/status/",
        {"status": "ACCEPTED"},
        format="json",
    )
    assert response.status_code == 403


def test_student_may_withdraw_own_application(auth, student, vacancy):
    from apps.jobs.services import apply_to_vacancy

    application = apply_to_vacancy(student=student, vacancy=vacancy)

    response = auth(student).post(
        f"/api/v1/jobs/applications/{application.id}/status/",
        {"status": "WITHDRAWN"},
        format="json",
    )
    assert response.status_code == 200
    application.refresh_from_db()
    assert application.status == "WITHDRAWN"


def test_student_cannot_create_a_skill_in_the_taxonomy(auth, student, taxonomy):
    """The taxonomy is centrally managed (prompt §21)."""
    response = auth(student).post(
        "/api/v1/taxonomy/skills/",
        {"slug": "rogue", "name_uz": "Rogue", "category": str(taxonomy["python"].category_id)},
        format="json",
    )
    assert response.status_code == 403


def test_employer_cannot_edit_another_companys_vacancy(
    auth, other_employer, vacancy
):
    response = auth(other_employer).patch(
        f"/api/v1/jobs/vacancies/{vacancy.id}/",
        {"title": "Hijacked"},
        format="json",
    )
    assert response.status_code in {403, 404}
    vacancy.refresh_from_db()
    assert vacancy.title != "Hijacked"


def test_employer_cannot_moderate_their_own_vacancy(auth, employer, vacancy):
    """Self-approval would make moderation theatre."""
    response = auth(employer).post(
        f"/api/v1/jobs/vacancies/{vacancy.id}/moderate/",
        {"approve": True},
        format="json",
    )
    assert response.status_code == 403


def test_candidate_search_is_pseudonymised_without_consent(
    auth, employer, other_student, vacancy, taxonomy, give_skill
):
    """TZ §12 minimal disclosure: a name is not handed out by default."""
    give_skill(other_student, taxonomy["sql"], 80)
    give_skill(other_student, taxonomy["power_bi"], 70)

    response = auth(employer).get(f"/api/v1/jobs/vacancies/{vacancy.id}/candidates/")
    assert response.status_code == 200

    rows = response.json()["results"]
    assert rows, "expected the candidate to be ranked"
    row = next(r for r in rows if r["user_id"] == str(other_student.id))
    assert row["identified"] is False
    assert row["name"] is None
    # The match itself is still visible — that is the point of the search.
    assert row["match"]["overall"] > 0


def test_candidate_is_identified_after_applying(
    auth, employer, student, vacancy, taxonomy, give_skill
):
    from apps.jobs.services import apply_to_vacancy

    give_skill(student, taxonomy["sql"], 80)
    apply_to_vacancy(student=student, vacancy=vacancy)

    response = auth(employer).get(f"/api/v1/jobs/vacancies/{vacancy.id}/candidates/")
    row = next(
        r for r in response.json()["results"] if r["user_id"] == str(student.id)
    )
    assert row["identified"] is True
    assert row["name"] == "Test Student"
