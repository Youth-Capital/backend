"""One candidate, opened in full by the employer.

The page behind this endpoint exists so an employer can decide about a person
rather than about a percentage — what they wrote about themselves, which
skills they hold, what proof sits behind each one.

That makes it the widest personal-data surface in the product, so the tests
that matter most here are the ones about what it refuses to say. A card that
withholds the surname while naming the university and the last employer has
not protected anybody: in a country this size those two facts are the person.
So the anonymous version has to be checked field by field, not by asserting
that `name` is null.

Reachability is the other half. The URL takes a user id, and without a check
that the id is actually a candidate for this vacancy, a paid candidate search
would double as a profile reader for any id an employer cared to type.
"""

import pytest

from apps.common.enums import EvidenceSource, ModerationStatus, RequirementLevel

pytestmark = pytest.mark.django_db

VACANCIES_URL = "/api/v1/jobs/vacancies/"


@pytest.fixture
def candidate(db, student, taxonomy, give_skill):
    """A learner who matches the vacancy, with a filled-in profile."""
    from apps.experience.models import Experience, ExperienceType

    profile = student.student_profile
    profile.bio = "Учусь на аналитика, делаю пет-проекты на SQL."
    profile.institution = "ТУИТ"
    profile.city = "Ташкент"
    profile.save()

    give_skill(student, taxonomy["sql"], 82, EvidenceSource.TEST)
    give_skill(student, taxonomy["power_bi"], 61, EvidenceSource.COURSE)

    Experience.objects.create(
        user=student,
        type=ExperienceType.INTERNSHIP,
        title="Аналитик-стажёр",
        organization="Secret Corp",
        start_date="2025-01-01",
        end_date="2025-07-01",
    )
    return student


@pytest.fixture
def matched(db, candidate, vacancy):
    """Make sure a MatchResult exists — the endpoint keys off it."""
    from apps.matching.services import compute_and_store_match

    return compute_and_store_match(candidate, vacancy)


def detail_url(vacancy, user):
    return f"{VACANCIES_URL}{vacancy.id}/candidates/{user.id}/"


def consent_to_talent_search(user):
    from apps.accounts.models import ConsentType
    from apps.accounts.services import grant_consent

    grant_consent(user, ConsentType.TALENT_SEARCH)


# -- what the owner sees ---------------------------------------------------
def test_the_owner_gets_the_whole_candidate(
    auth, employer, vacancy, candidate, matched
):
    consent_to_talent_search(candidate)

    response = auth(employer).get(detail_url(vacancy, candidate))

    assert response.status_code == 200, response.data
    body = response.data
    assert body["identified"] is True
    assert body["name"]
    assert body["bio"]
    assert body["match"]["overall"] >= 0
    assert {row["skill"] for row in body["skills"]}, "no skills came back"


def test_each_skill_carries_the_proof_behind_it(
    auth, employer, vacancy, candidate, matched
):
    """The number is worthless to an employer without "says who?"."""
    consent_to_talent_search(candidate)

    response = auth(employer).get(detail_url(vacancy, candidate))

    sql = next(row for row in response.data["skills"] if row["skill"] == "SQL")
    assert sql["evidence"], "a verified skill arrived with no evidence trail"
    assert sql["evidence"][0]["source"] == EvidenceSource.TEST
    assert sql["evidence"][0]["weight"] > 0


# -- what an anonymous candidate must not leak -----------------------------
def test_an_anonymous_candidate_keeps_every_identifying_field(
    auth, employer, vacancy, candidate, matched
):
    """The field-by-field check.

    No consent, no application, no public profile — so this is talent search,
    and talent search shows capability, not identity.
    """
    response = auth(employer).get(detail_url(vacancy, candidate))

    body = response.data
    assert body["identified"] is False
    assert body["name"] is None
    assert body["avatar"] is None
    assert body["bio"] == "", "free text they wrote about themselves leaked"
    assert body["institution"] == "", "the university names the person"
    assert body["city"] == ""
    for entry in body["experience_entries"]:
        assert entry["organization"] is None, "the employer names the person"
    for certificate in body["certificates"]:
        assert certificate["serial"] is None, (
            "the serial resolves to a public page carrying the holder's name"
        )


def test_capability_is_still_visible_without_identity(
    auth, employer, vacancy, candidate, matched
):
    """Anonymising must not empty the card — that would defeat talent search."""
    response = auth(employer).get(detail_url(vacancy, candidate))

    body = response.data
    assert body["youth_id"], "the pseudonym itself must be present"
    assert body["skills"], "skills are what talent search is for"
    assert body["match"]["overall"] >= 0
    assert any(entry["title"] for entry in body["experience_entries"])


def test_applying_to_this_company_reveals_the_name(
    auth, employer, vacancy, candidate, matched
):
    """Identity follows the application, without any consent flag."""
    from apps.jobs.models import Application

    Application.objects.create(student=candidate, vacancy=vacancy)

    response = auth(employer).get(detail_url(vacancy, candidate))

    assert response.data["identified"] is True
    assert response.data["name"]
    assert response.data["has_applied"] is True


# -- reachability ----------------------------------------------------------
def test_another_companys_candidate_cannot_be_opened(
    auth, other_employer, vacancy, candidate, matched
):
    response = auth(other_employer).get(detail_url(vacancy, candidate))

    assert response.status_code in {403, 404}


def test_a_person_who_is_not_a_candidate_cannot_be_opened(
    auth, employer, vacancy, other_student, candidate, matched
):
    """The endpoint is not a profile reader for arbitrary user ids."""
    response = auth(employer).get(detail_url(vacancy, other_student))

    assert response.status_code in {403, 404}


def test_a_student_cannot_read_a_candidate_card(
    auth, student, vacancy, candidate, matched
):
    response = auth(student).get(detail_url(vacancy, candidate))

    assert response.status_code in {403, 404}


# -- the audit trail -------------------------------------------------------
def test_opening_a_named_profile_is_recorded(
    auth, employer, vacancy, candidate, matched
):
    """Personal-data access is auditable, not merely permitted."""
    from apps.audit.models import AuditAction, AuditLog

    consent_to_talent_search(candidate)
    before = AuditLog.objects.filter(action=AuditAction.PII_ACCESS).count()

    auth(employer).get(detail_url(vacancy, candidate))

    assert AuditLog.objects.filter(action=AuditAction.PII_ACCESS).count() == before + 1


def test_opening_an_anonymous_card_is_not_a_pii_access(
    auth, employer, vacancy, candidate, matched
):
    """Nothing personal was shown, so nothing personal is logged."""
    from apps.audit.models import AuditAction, AuditLog

    before = AuditLog.objects.filter(action=AuditAction.PII_ACCESS).count()

    auth(employer).get(detail_url(vacancy, candidate))

    assert AuditLog.objects.filter(action=AuditAction.PII_ACCESS).count() == before


# -- the CV block ----------------------------------------------------------
@pytest.fixture
def candidate_cv(db, candidate):
    from apps.cv.models import CVDocument

    return CVDocument.objects.create(
        user=candidate,
        title="CV",
        headline="Junior data analyst",
        summary="Аналитик-стажёр, SQL и Power BI.",
        is_primary=True,
    )


def test_the_card_carries_the_cv_and_its_rating(
    auth, employer, vacancy, candidate, matched, candidate_cv
):
    """The employer asked for the rating *and* the résumé — both are here."""
    consent_to_talent_search(candidate)

    response = auth(employer).get(detail_url(vacancy, candidate))

    cv = response.data["cv"]
    assert cv is not None
    assert 0 < cv["rating"]["overall"] <= 100
    assert cv["rating"]["band"]
    assert len(cv["rating"]["components"]) == 6
    assert cv["document"]["summary"]
    assert cv["document"]["personal"]["full_name"]


def test_an_anonymous_candidates_cv_is_redacted_too(
    auth, employer, vacancy, candidate, matched, candidate_cv
):
    """The résumé is the widest identity surface on the card.

    Sending the rating with an unredacted document attached would undo every
    other rule on this endpoint in one field.
    """
    response = auth(employer).get(detail_url(vacancy, candidate))

    cv = response.data["cv"]
    document = cv["document"]

    assert response.data["identified"] is False
    assert document["meta"]["identified"] is False
    assert document["personal"]["full_name"] == ""
    assert document["personal"]["city"] == ""
    assert "contacts" not in document, "an email address is a name"
    for entry in document.get("experience", []):
        assert entry["organization"] == ""

    # The rating still comes through: it is a number about a document, not
    # about a person, and it is the reason the block exists.
    assert cv["rating"]["overall"] > 0
    assert document.get("skills"), "capability must survive the redaction"


def test_a_candidate_without_a_cv_returns_null_rather_than_failing(
    auth, employer, vacancy, candidate, matched
):
    response = auth(employer).get(detail_url(vacancy, candidate))

    assert response.status_code == 200
    assert response.data["cv"] is None


def test_the_candidate_list_carries_the_stored_cv_rating(
    auth, employer, vacancy, candidate, matched, candidate_cv
):
    """Sorting forty candidates needs the number on the row, not a click."""
    from apps.cv.rating import refresh_cv_rating

    refresh_cv_rating(candidate_cv)

    response = auth(employer).get(f"{VACANCIES_URL}{vacancy.id}/candidates/")

    row = next(
        item
        for item in response.data["results"]
        if item["user_id"] == str(candidate.id)
    )
    assert row["has_cv"] is True
    assert row["cv_rating"] == candidate_cv.quality_score
