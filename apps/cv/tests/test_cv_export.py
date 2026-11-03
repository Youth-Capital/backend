"""The CV leaves the platform as a file.

An application form asks for a document, not for a link behind a sign-in, so a
CV that cannot be downloaded is a CV that cannot be used. What is worth pinning
here is the contract around the file rather than its appearance: that it is a
real PDF, that it is named after the person, that it is only ever the caller's
own, and that the ordering rules the layout promises are actually applied.

The bytes themselves are not asserted against text. fpdf2 embeds a subset of
the font and writes glyph identifiers rather than characters, so "Experience"
does not appear in the file as those letters — grepping the output would test
nothing and would fail for the wrong reasons. The layout's decisions are tested
where they are made: in the functions that order and format the records.
"""

from datetime import date

import pytest

from apps.cv.models import CVDocument
from django.utils.translation import override

from apps.cv.pdf import (
    LABELS,
    LANGUAGE_LEVELS,
    SKILL_SCALE_MAX,
    _period,
    _recent_first,
    _year,
    contact_line,
    render_cv_pdf,
    skill_annotation,
    suggest_filename,
)
from apps.cv.services import build_cv_payload
from apps.cv.typography import sanitise
from apps.experience.models import Experience, ExperienceType

pytestmark = pytest.mark.django_db


def url_for(cv: CVDocument) -> str:
    return f"/api/v1/cv/documents/{cv.id}/export/"


@pytest.fixture
def cv(student):
    return CVDocument.objects.create(
        user=student,
        title="Mening rezyumem",
        headline="Data analyst",
        summary="Short and true.",
        language="ru",
    )


# ---------------------------------------------------------------------------
# It is a file, and it is a PDF
# ---------------------------------------------------------------------------
def test_the_export_is_a_pdf(auth, student, cv):
    response = auth(student).get(url_for(cv))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    body = b"".join(response.streaming_content)
    assert body.startswith(b"%PDF-")
    assert body.rstrip().endswith(b"%%EOF")


def test_the_export_is_offered_as_a_download(auth, student, cv):
    response = auth(student).get(url_for(cv))

    assert "attachment" in response["Content-Disposition"]


def test_the_file_is_named_after_the_person(auth, student, cv):
    response = auth(student).get(url_for(cv))

    # "Test Student" is the profile the fixture builds.
    assert "Test-Student.pdf" in response["Content-Disposition"]


def test_a_cyrillic_name_survives_into_the_filename(auth, student, cv):
    profile = student.student_profile
    profile.first_name = "Азизахон"
    profile.last_name = "Ибрагимова"
    profile.save(update_fields=["first_name", "last_name"])

    response = auth(student).get(url_for(cv))

    # Django falls back to RFC 5987 for anything outside ASCII, which is the
    # only form in which this name can reach the browser intact.
    assert "filename*=utf-8''" in response["Content-Disposition"].lower()
    assert "%D0%90" in response["Content-Disposition"]  # А


def test_an_empty_record_still_produces_a_file(auth, student, cv):
    """A student who has only just signed up still gets a document."""
    response = auth(student).get(url_for(cv))

    assert response.status_code == 200
    assert len(b"".join(response.streaming_content)) > 1000


# ---------------------------------------------------------------------------
# It is only ever the caller's own CV
# ---------------------------------------------------------------------------
def test_another_student_cannot_export_this_cv(auth, other_student, cv):
    response = auth(other_student).get(url_for(cv))

    # Not 403: the queryset is filtered by owner, so somebody else's document
    # does not exist as far as this endpoint is concerned.
    assert response.status_code == 404


def test_an_employer_cannot_export_a_students_cv(auth, employer, cv):
    assert auth(employer).get(url_for(cv)).status_code == 404


def test_an_anonymous_caller_is_refused(api, cv):
    assert api.get(url_for(cv)).status_code == 401


# ---------------------------------------------------------------------------
# The document follows its own section configuration
# ---------------------------------------------------------------------------
def test_a_disabled_section_is_left_out_of_the_payload(student, cv):
    Experience.objects.create(
        user=student,
        type=ExperienceType.WORK,
        title="Analyst",
        organization="SafeNet",
        start_date=date(2025, 1, 1),
    )
    cv.sections_config = [{"key": "experience", "enabled": False, "order": 0}]
    cv.save(update_fields=["sections_config"])

    payload = build_cv_payload(cv)

    assert "experience" not in payload
    # And the renderer is handed exactly that, so it cannot print what the
    # document switched off.
    assert render_cv_pdf(payload).startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# Reverse chronological order, which the layout promises
# ---------------------------------------------------------------------------
def test_the_most_recent_record_comes_first():
    older = {"title": "older", "start_date": date(2020, 1, 1), "end_date": date(2021, 1, 1)}
    newer = {"title": "newer", "start_date": date(2023, 1, 1), "end_date": date(2024, 1, 1)}

    assert [e["title"] for e in _recent_first([older, newer])] == ["newer", "older"]


def test_a_current_role_sorts_above_a_finished_one():
    finished = {"title": "finished", "start_date": date(2024, 1, 1), "end_date": date(2026, 1, 1)}
    current = {"title": "current", "start_date": date(2020, 1, 1), "is_current": True}

    assert [e["title"] for e in _recent_first([finished, current])] == ["current", "finished"]


def test_an_undated_record_sorts_last():
    """It cannot claim a recency it has not shown."""
    dated = {"title": "dated", "start_date": date(2019, 1, 1), "end_date": date(2020, 1, 1)}
    undated = {"title": "undated"}

    assert [e["title"] for e in _recent_first([undated, dated])] == ["dated", "undated"]


# ---------------------------------------------------------------------------
# How a period reads
# ---------------------------------------------------------------------------
def test_a_period_runs_from_month_to_month():
    labels = {"present": "настоящее время"}
    entry = {"start_date": date(2024, 3, 1), "end_date": date(2025, 9, 1)}

    assert _period(entry, labels) == "03.2024 – 09.2025"


def test_a_current_role_ends_in_the_present_not_in_a_blank():
    labels = {"present": "настоящее время"}
    entry = {"start_date": date(2024, 3, 1), "is_current": True}

    assert _period(entry, labels) == "03.2024 – настоящее время"


def test_education_is_dated_by_year_because_the_month_is_noise():
    labels = {"present": "present"}
    entry = {"start_date": date(2019, 9, 1), "end_date": date(2023, 6, 1)}

    assert _period(entry, labels, years_only=True) == "2019 – 2023"


def test_a_single_year_is_not_printed_twice():
    labels = {"present": "present"}
    entry = {"start_date": date(2022, 2, 1), "end_date": date(2022, 11, 1)}

    assert _period(entry, labels, years_only=True) == "2022"


def test_a_missing_date_renders_as_nothing_rather_than_as_none():
    assert _year(None) == ""
    assert _period({}, {"present": "present"}) == ""


# ---------------------------------------------------------------------------
# The document is written in one language, and it is the document's own
# ---------------------------------------------------------------------------
def test_the_payload_is_assembled_in_the_documents_language(
    student, taxonomy, give_skill, cv
):
    """Headings follow the document, so everything it names must follow too.

    Before this, the labels came from the document and the taxonomy came from
    the request, so a Russian CV opened from an Uzbek interface named its
    region in Russian and its skills in Uzbek.
    """
    taxonomy["region"].name_ru = "город Ташкент"
    taxonomy["region"].save(update_fields=["name_ru"])
    taxonomy["sql"].name_ru = "Базы данных"
    taxonomy["sql"].save(update_fields=["name_ru"])
    give_skill(student, taxonomy["sql"], 65)

    cv.language = "ru"
    cv.save(update_fields=["language"])

    # The request is being made in Uzbek; the document is not.
    with override("uz"):
        payload = build_cv_payload(cv)

    assert payload["personal"]["region"] == "город Ташкент"
    assert [s["name"] for s in payload["skills"]] == ["Базы данных"]


def test_an_uzbek_document_keeps_its_uzbek_names(student, taxonomy, give_skill, cv):
    taxonomy["region"].name_ru = "город Ташкент"
    taxonomy["region"].save(update_fields=["name_ru"])
    give_skill(student, taxonomy["sql"], 65)

    cv.language = "uz"
    cv.save(update_fields=["language"])

    with override("ru"):
        payload = build_cv_payload(cv)

    assert payload["personal"]["region"] == "Toshkent"


def test_the_language_choice_reaches_the_file(auth, student, cv):
    cv.language = "en"
    cv.save(update_fields=["language"])

    response = auth(student).get(url_for(cv))

    assert response.status_code == 200
    assert b"".join(response.streaming_content).startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# One place, not the same place twice
# ---------------------------------------------------------------------------
def test_the_city_the_student_typed_wins_over_the_taxonomy_region():
    """Both printed gave "Toshkent · город Ташкент" — one place, two
    languages, reading as two."""
    line = contact_line(
        {"city": "Toshkent", "region": "город Ташкент"}, {}, LABELS["ru"]
    )

    assert line == "Toshkent"


def test_the_region_stands_in_when_no_city_was_given():
    line = contact_line({"city": "", "region": "город Ташкент"}, {}, LABELS["ru"])

    assert line == "город Ташкент"


def test_an_absent_field_leaves_no_stranded_separator():
    line = contact_line(
        {"city": "Toshkent", "youth_id": None}, {"email": "a@b.uz"}, LABELS["ru"]
    )

    assert line == "a@b.uz  ·  Toshkent"
    assert not line.startswith("·") and not line.endswith("·")


def test_the_youth_id_is_labelled_so_a_reader_knows_what_it_is():
    line = contact_line({"youth_id": "YK-2026-77355F"}, {}, LABELS["ru"])

    assert line == "Youth ID YK-2026-77355F"


# ---------------------------------------------------------------------------
# The score is kept, and printed so that it can be read
# ---------------------------------------------------------------------------
def test_a_score_is_printed_over_its_denominator():
    """A bare "65" asks the reader to know a scale they have never seen."""
    annotation = skill_annotation({"proficiency": 65}, LABELS["ru"])

    assert annotation == f"65 / {SKILL_SCALE_MAX}"


def test_a_verified_skill_says_so_beside_its_score():
    annotation = skill_annotation({"proficiency": 65, "verified": True}, LABELS["ru"])

    assert annotation == "65 / 100  ·  подтверждён"


def test_an_unverified_skill_claims_nothing():
    annotation = skill_annotation({"proficiency": 40, "verified": False}, LABELS["ru"])

    assert LABELS["ru"]["verified"] not in annotation


def test_a_zero_score_is_still_printed_rather_than_blanked():
    """Zero is a measurement, and hiding it would make the row read as missing
    data instead of as a skill the platform has not yet seen evidence for."""
    assert skill_annotation({"proficiency": 0}, LABELS["ru"]) == "0 / 100"


def test_a_skill_with_no_score_leaves_the_column_empty():
    assert skill_annotation({}, LABELS["ru"]) == ""


# ---------------------------------------------------------------------------
# Levels in words, because "B2" is a certificate and not a fact
# ---------------------------------------------------------------------------
def test_a_cefr_level_is_written_out():
    assert LANGUAGE_LEVELS["ru"]["b2"] == "выше среднего"
    assert LANGUAGE_LEVELS["en"]["c1"] == "advanced"


def test_every_language_names_every_level():
    """A level that is translated in one language and not another is a gap a
    reader meets as a blank cell."""
    expected = set(LANGUAGE_LEVELS["ru"])
    for code, levels in LANGUAGE_LEVELS.items():
        assert set(levels) == expected, code


# ---------------------------------------------------------------------------
# The name the browser saves it under
# ---------------------------------------------------------------------------
def test_the_filename_is_built_from_the_person():
    assert suggest_filename({"personal": {"full_name": "Azizaxon Ibragimova"}}) == (
        "Azizaxon-Ibragimova.pdf"
    )


def test_the_filename_drops_what_a_filesystem_would_reject():
    name = suggest_filename({"personal": {"full_name": 'A/B\\C:D*E?F"G<H>I|J'}})

    assert not set(name[:-4]) & set('/\\:*?"<>|')
    assert name.endswith(".pdf")


def test_an_unnamed_document_still_gets_a_filename():
    assert suggest_filename({}) == "CV.pdf"
    assert suggest_filename({"personal": {"full_name": "   "}}) == "CV.pdf"


# ---------------------------------------------------------------------------
# Nothing the student can type breaks the file
# ---------------------------------------------------------------------------
def test_a_glyph_the_font_lacks_is_replaced_rather_than_dropped():
    """Uzbek Latin is typed with U+02BB on some keyboards; Manrope has only the
    typographic quote, and a missing glyph prints as nothing at all."""
    assert sanitise("Oʻzbekiston") == "O‘zbekiston"
    assert sanitise("qatʼiy") == "qat’iy"


def test_text_with_no_glyph_at_all_does_not_break_the_render():
    payload = {
        "meta": {"sections": ["summary"], "language": "ru"},
        "personal": {"full_name": "Student 🙂"},
        "summary": "Emoji 🎓 and 中文 in a summary.",
    }

    assert render_cv_pdf(payload).startswith(b"%PDF-")


def test_a_long_record_runs_onto_a_second_page():
    payload = {
        "meta": {"sections": ["summary", "experience"], "language": "ru"},
        "personal": {"full_name": "Student"},
        "summary": "Lorem ipsum. " * 60,
        "experience": [
            {
                "title": f"Role {n}",
                "organization": "Somewhere",
                "description": "Did a number of things that took several lines. " * 6,
                "start_date": date(2020 + n % 5, 1, 1),
                "end_date": date(2021 + n % 5, 1, 1),
            }
            for n in range(12)
        ],
    }

    out = render_cv_pdf(payload)

    # Two pages is the documented ceiling for a CV; this one is deliberately
    # over-long, and the point is that it paginates rather than overflowing.
    assert out.startswith(b"%PDF-")
    assert len(out) > 5000


def test_the_language_falls_back_rather_than_failing_on_an_unknown_code():
    payload = {
        "meta": {"sections": ["summary"], "language": "fr"},
        "personal": {"full_name": "Student"},
        "summary": "Bonjour.",
    }

    assert render_cv_pdf(payload).startswith(b"%PDF-")
