"""CV quality rating."""

import pytest

from apps.common.enums import EvidenceSource
from apps.cv.models import CVDocument, PortfolioItem
from apps.cv.rating import WEIGHTS, refresh_cv_rating, score_cv


@pytest.fixture
def cv(db, student):
    return CVDocument.objects.create(user=student, title="My CV", is_primary=True)


def test_weights_sum_to_100():
    """Every stored score depends on this, so it is asserted rather than
    assumed."""
    assert sum(WEIGHTS.values()) == 100


def test_empty_cv_scores_low(cv):
    result = score_cv(cv)

    assert result["overall"] < 30
    assert result["band"] == "EMPTY"
    # An empty CV must say what to do first, not merely that it is empty.
    assert any(tip["severity"] == "high" for tip in result["tips"])


def test_component_scores_never_exceed_their_ceiling(cv, taxonomy, give_skill):
    for skill in (taxonomy["python"], taxonomy["sql"], taxonomy["power_bi"]):
        give_skill(cv.user, skill, 95, EvidenceSource.EMPLOYER)

    result = score_cv(cv)
    for component in result["components"]:
        assert component["score"] <= component["max"]
        assert component["max"] == WEIGHTS[component["key"]]
    assert result["overall"] <= 100


def test_filling_the_document_raises_the_score(cv):
    before = score_cv(cv)["overall"]

    cv.headline = "Junior data analyst"
    cv.summary = "x" * 250
    cv.save()

    assert score_cv(cv)["overall"] > before


def test_verified_skills_outweigh_declared_ones(db, student, other_student, taxonomy):
    """The point of the whole formula: proof beats claims.

    Two CVs, the same three skills at the same level. One person tested them,
    the other typed them in. The tested CV must score higher, or the rating
    rewards confident self-reporting — the failure mode the platform is built
    to avoid.
    """
    from apps.profiles.services import record_skill_evidence

    declared = CVDocument.objects.create(user=student, title="A", is_primary=True)
    proven = CVDocument.objects.create(user=other_student, title="B", is_primary=True)

    for skill in (taxonomy["python"], taxonomy["sql"], taxonomy["power_bi"]):
        record_skill_evidence(
            user=student, skill=skill, source=EvidenceSource.SELF, score=90
        )
        record_skill_evidence(
            user=other_student, skill=skill, source=EvidenceSource.TEST, score=90
        )

    assert score_cv(proven)["overall"] > score_cv(declared)["overall"]


def test_portfolio_item_without_substance_counts_less(cv):
    PortfolioItem.objects.create(user=cv.user, title="Bare", is_public=True)
    bare = next(c for c in score_cv(cv)["components"] if c["key"] == "portfolio")

    PortfolioItem.objects.create(
        user=cv.user,
        title="Real",
        description="What it does and how.",
        url="https://example.uz/project",
        is_public=True,
    )
    described = next(c for c in score_cv(cv)["components"] if c["key"] == "portfolio")

    assert described["score"] > bare["score"]


def test_refresh_persists_the_score(cv):
    result = refresh_cv_rating(cv)

    cv.refresh_from_db()
    assert cv.quality_score == result["overall"]
    assert cv.quality_computed_at is not None
    assert cv.quality_breakdown["components"]


def test_rating_endpoint_returns_breakdown(cv, auth):
    client = auth(cv.user)
    response = client.get(f"/api/v1/cv/documents/{cv.id}/rating/")

    assert response.status_code == 200
    assert set(response.data) >= {"overall", "band", "components", "tips"}
    assert len(response.data["components"]) == len(WEIGHTS)


def test_rating_endpoint_is_owner_only(cv, other_student, auth):
    client = auth(other_student)
    assert client.get(f"/api/v1/cv/documents/{cv.id}/rating/").status_code == 404


# ---------------------------------------------------------------------------
# What an employer sees before they have earned a name
# ---------------------------------------------------------------------------
def test_unidentified_cv_keeps_capability_and_drops_identity(
    db, student, taxonomy, give_skill
):
    """The redaction rule, on the résumé itself.

    A "pseudonymised" CV that still names the school, the last employer and an
    email address is not pseudonymised. Capability — skills, levels, how long
    somebody worked — is the part talent search is allowed to see, so it must
    survive; everything that points at the person must not.
    """
    from apps.cv.services import build_cv_payload
    from apps.experience.models import Experience, ExperienceType
    from apps.profiles.models import Education

    cv = CVDocument.objects.create(user=student, title="CV", is_primary=True)
    give_skill(student, taxonomy["sql"], 80)
    Education.objects.create(
        user=student,
        institution="Toshkent axborot texnologiyalari universiteti",
        degree="Bachelor",
        field_of_study="CS",
        start_date="2023-09-01",
    )
    Experience.objects.create(
        user=student,
        type=ExperienceType.INTERNSHIP,
        title="Data Intern",
        organization="PayNur Fintech",
        start_date="2025-01-01",
        end_date="2025-06-01",
    )

    open_view = build_cv_payload(cv, identified=True)
    blind = build_cv_payload(cv, identified=False)

    # Identity is gone.
    assert blind["personal"]["full_name"] == ""
    assert blind["personal"]["city"] == ""
    assert blind["personal"]["avatar"] is None
    assert "contacts" not in blind
    assert all(entry["institution"] == "" for entry in blind["education"])
    assert all(entry["organization"] == "" for entry in blind["experience"])
    assert blind["meta"]["identified"] is False

    # Capability is not.
    assert [s["name"] for s in blind["skills"]] == [
        s["name"] for s in open_view["skills"]
    ]
    assert blind["experience"][0]["title"] == "Data Intern"
    assert blind["experience"][0]["duration_months"] > 0


def test_certificate_serial_is_part_of_the_identity(db, student, taxonomy):
    """A serial resolves to a public page carrying the holder's name."""
    from apps.common.enums import ModerationStatus
    from apps.cv.services import build_cv_payload
    from apps.learning.models import Certificate, Course

    cv = CVDocument.objects.create(user=student, title="CV", is_primary=True)
    course = Course.objects.create(
        title="SQL Fundamentals",
        description="",
        author=student,
        status=ModerationStatus.PUBLISHED,
    )
    Certificate.objects.create(
        user=student, course=course, serial="YK-CERT-0001", verification_code="abc123"
    )

    blind = build_cv_payload(cv, identified=False)

    assert blind["certificates"][0]["title"] == "SQL Fundamentals"
    assert blind["certificates"][0]["serial"] == ""
    assert blind["certificates"][0]["verification_code"] == ""
