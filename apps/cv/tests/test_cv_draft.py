"""The assistant writes a CV from the student's own record, and only theirs.

Two ways to fill the form: type it, or ask for a draft. The draft is written
from skills, experience and finished courses — so the two things worth pinning
are that it reaches no other student's rows, and that it says nothing the
record does not contain.

It also has to work with no model configured, because that is how the platform
runs by default: a feature that needs an API key to do anything is a feature
most of these students never see.
"""

from datetime import date

import pytest

from apps.cv.composer import collect_evidence, draft_cv
from apps.cv.models import CVDocument
from apps.experience.models import Experience, ExperienceType
from apps.learning.models import Course, Enrollment, EnrollmentStatus

pytestmark = pytest.mark.django_db

URL = "/api/v1/cv/documents/draft/"


@pytest.fixture
def with_record(student, taxonomy, give_skill):
    """A student with something to write about: a skill, a job, a course."""
    give_skill(student, taxonomy["python"], 72)
    Experience.objects.create(
        user=student,
        type=ExperienceType.WORK,
        title="Junior analyst",
        organization="SafeNet",
        description="Dashboards and weekly reports.",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 9, 1),
    )
    course = Course.objects.create(
        title="SQL for Data Analysis", slug="sql-draft", author=student
    )
    Enrollment.objects.create(
        user=student, course=course, status=EnrollmentStatus.COMPLETED, progress=100
    )
    return student


# ---------------------------------------------------------------------------
# It is written from the student's own record
# ---------------------------------------------------------------------------
def test_the_draft_names_the_skills_the_record_holds(with_record, taxonomy):
    result = draft_cv(with_record)

    assert result["available"] is True
    assert taxonomy["python"].name in result["headline"] + result["summary"]


def test_the_draft_counts_the_experience_the_record_holds(with_record):
    result = draft_cv(with_record)

    assert "8" in result["summary"], result["summary"]


def test_the_draft_mentions_a_finished_course(with_record):
    result = draft_cv(with_record)

    assert "SQL for Data Analysis" in result["summary"]


def test_an_unfinished_course_is_not_a_qualification(student, taxonomy, give_skill):
    """Half a course is not something to put on a CV."""
    give_skill(student, taxonomy["python"], 60)
    course = Course.objects.create(title="Half done", slug="half-done", author=student)
    Enrollment.objects.create(
        user=student, course=course, status=EnrollmentStatus.IN_PROGRESS, progress=40
    )

    result = draft_cv(student)

    assert "Half done" not in result["summary"]


def test_nobody_elses_record_reaches_the_draft(student, other_student, taxonomy, give_skill):
    give_skill(other_student, taxonomy["python"], 95)
    Experience.objects.create(
        user=other_student,
        type=ExperienceType.WORK,
        title="Chief of everything",
        organization="Somewhere else",
        start_date=date(2018, 1, 1),
        end_date=date(2025, 1, 1),
    )

    evidence = collect_evidence(student)

    assert evidence["experience"] == []
    assert evidence["skills"] == []


def test_an_empty_profile_is_told_so_rather_than_given_two_empty_sentences(student):
    result = draft_cv(student)

    assert result["available"] is False
    assert result["reason"] == "no_evidence"
    assert result["summary"] == ""


def test_nothing_is_saved_by_drafting(with_record):
    before = CVDocument.objects.count()

    draft_cv(with_record)

    assert CVDocument.objects.count() == before


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------
def test_a_student_gets_a_draft(auth, with_record):
    response = auth(with_record).post(URL, {}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["available"] is True
    assert response.data["headline"]
    assert response.data["summary"]


def test_the_draft_says_which_rows_it_was_written_from(auth, with_record):
    """A sentence about yourself should be checkable against the record."""
    response = auth(with_record).post(URL, {}, format="json")

    evidence = response.data["evidence"]
    assert evidence["courses"][0]["title"] == "SQL for Data Analysis"
    assert evidence["experience"][0]["title"] == "Junior analyst"


def test_an_employer_has_no_cv_to_draft(auth, employer):
    response = auth(employer).post(URL, {}, format="json")

    assert response.status_code == 403


def test_signing_out_is_enough_to_be_refused(api):
    assert api.post(URL, {}, format="json").status_code in {401, 403}


def test_the_draft_is_metered(auth, with_record, monkeypatch):
    """It costs a model round trip once a provider is wired, so it is counted."""
    spent = []
    import apps.billing.services as billing

    monkeypatch.setattr(
        billing, "consume", lambda user, feature, **kw: spent.append(feature)
    )

    auth(with_record).post(URL, {}, format="json")

    assert spent, "the drafting endpoint did not charge the feature"


# ---------------------------------------------------------------------------
# The model path
# ---------------------------------------------------------------------------
def test_with_no_provider_the_platform_writes_it_itself(with_record, monkeypatch):
    """The default configuration still produces a usable draft."""
    monkeypatch.setattr("apps.cv.composer.get_chat_backend", lambda: None)

    result = draft_cv(with_record)

    assert result["available"] is True
    assert result["source"] == "platform"
    assert result["summary"]


def test_a_models_draft_is_used_when_one_answers(with_record, monkeypatch):
    class Backend:
        def is_ready(self):
            return True

        def run_task(self, **kwargs):
            self.seen = kwargs
            return '{"headline": "Data analyst · SQL", "summary": "I read data."}'

    backend = Backend()
    monkeypatch.setattr("apps.cv.composer.get_chat_backend", lambda: backend)

    result = draft_cv(with_record)

    assert result["source"] == "model"
    assert result["headline"] == "Data analyst · SQL"
    # The record goes in as material, never folded into the instructions —
    # the student's own experience text is user input, and this is the
    # boundary the AI hardening work put there.
    assert "material" in backend.seen
    assert "Junior analyst" not in backend.seen["instructions"]
    assert "Junior analyst" in backend.seen["material"][0][1]


def test_a_reply_this_cannot_parse_falls_back_rather_than_failing(with_record, monkeypatch):
    class Backend:
        def is_ready(self):
            return True

        def run_task(self, **kwargs):
            return "Sure! Here is your CV: ..."

    monkeypatch.setattr("apps.cv.composer.get_chat_backend", lambda: Backend())

    result = draft_cv(with_record)

    assert result["available"] is True
    assert result["source"] == "platform"


def test_a_rejected_reply_falls_back_rather_than_reaching_the_student(
    with_record, monkeypatch
):
    """Output vetting rejected it — the student gets the plain draft, not that."""
    from apps.ai import prompting

    class Backend:
        def is_ready(self):
            return True

        def run_task(self, **kwargs):
            raise prompting.OutputRejected("prompt_leak")

    monkeypatch.setattr("apps.cv.composer.get_chat_backend", lambda: Backend())

    result = draft_cv(with_record)

    assert result["source"] == "platform"


def test_a_provider_outage_does_not_take_the_feature_down(with_record, monkeypatch):
    class Backend:
        def is_ready(self):
            return True

        def run_task(self, **kwargs):
            raise RuntimeError("the provider is down")

    monkeypatch.setattr("apps.cv.composer.get_chat_backend", lambda: Backend())

    result = draft_cv(with_record)

    assert result["available"] is True
    assert result["source"] == "platform"
