"""Jobs posted somewhere else, which the platform only points at.

These are not vacancies on this platform and are deliberately not stored as
if they were: there is no employer account behind them, nothing to apply to
here, and no interview to arrange. What the product owes a student is an
honest card and a link that goes where it says it goes.

So the things worth pinning are the link and the list. That the address is
checked against an allowlist before it is ever shown to anybody, that the
import can be run again without producing a second card for the same job,
and that the file shipped with the repository actually passes its own rules —
a broken address in it would reach a student as a dead end.
"""

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.jobs.external import clean_vacancy_url, source_for
from apps.jobs.models import ExternalFit, ExternalVacancy, VacancySource

pytestmark = pytest.mark.django_db

URL = "/api/v1/jobs/external/"
DATA = Path(__file__).resolve().parents[1] / "data" / "external_vacancies.json"


def entry(**overrides):
    row = {
        "source": "OLX",
        "external_url": "https://www.olx.uz/obyavlenie/rabota/kurer-ID1.html",
        "title": "Курьер",
        "company": "",
        "city": "Ташкент",
        "schedule": "Свободный график",
        "pay": "5 млн",
        "fit": "FITS",
        "note": "",
        "collected_on": "2026-11-13",
    }
    row.update(overrides)
    return row


def write(tmp_path, rows):
    path = tmp_path / "vacancies.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# The address is checked before anybody is sent to it
# ---------------------------------------------------------------------------
def test_a_board_on_the_list_is_accepted():
    url = clean_vacancy_url("https://www.olx.uz/obyavlenie/rabota/kurer-ID1.html")

    assert url == "https://www.olx.uz/obyavlenie/rabota/kurer-ID1.html"


def test_a_board_that_is_not_on_the_list_is_refused():
    with pytest.raises(ValueError, match="platform_not_allowed"):
        clean_vacancy_url("https://example.com/jobs/1")


def test_a_front_page_is_not_a_vacancy():
    """Also what a mistyped link most often collapses to."""
    with pytest.raises(ValueError, match="not_a_vacancy_page"):
        clean_vacancy_url("https://cloz.uz/")


def test_a_non_url_is_refused():
    for raw in ("", "   ", "not a url", "javascript:alert(1)", "ftp://cloz.uz/x"):
        with pytest.raises(ValueError):
            clean_vacancy_url(raw)


def test_tracking_parameters_do_not_travel_to_the_student():
    url = clean_vacancy_url("https://cloz.uz/vacancies/kuryer-69252?utm_source=mail&ref=x")

    assert url == "https://cloz.uz/vacancies/kuryer-69252"


def test_the_source_is_derived_from_the_host():
    assert source_for("https://cloz.uz/vacancies/kuryer-69252") == "CLOZ"
    assert source_for("https://www.olx.uz/obyavlenie/rabota/x-ID1.html") == "OLX"
    assert source_for("https://career.habr.com/vacancies/1") == "HABR"
    assert source_for("https://t.me/unilance/7681") == "TELEGRAM"
    assert source_for("https://example.com/x") == ""


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------
def test_the_import_creates_the_listings(tmp_path):
    call_command("import_external_vacancies", file=write(tmp_path, [entry()]), verbosity=0)

    assert ExternalVacancy.objects.count() == 1
    row = ExternalVacancy.objects.get()
    assert row.title == "Курьер"
    assert row.source == VacancySource.OLX
    assert row.fit == ExternalFit.FITS


def test_running_it_again_does_not_produce_a_second_card(tmp_path):
    """The address is the identity: the same job posted twice is one job."""
    path = write(tmp_path, [entry()])
    call_command("import_external_vacancies", file=path, verbosity=0)
    call_command("import_external_vacancies", file=path, verbosity=0)

    assert ExternalVacancy.objects.count() == 1


def test_a_changed_listing_is_refreshed_rather_than_duplicated(tmp_path):
    call_command("import_external_vacancies", file=write(tmp_path, [entry()]), verbosity=0)
    call_command(
        "import_external_vacancies",
        file=write(tmp_path, [entry(pay="7 млн", title="Курьер (обновлено)")]),
        verbosity=0,
    )

    assert ExternalVacancy.objects.count() == 1
    assert ExternalVacancy.objects.get().pay == "7 млн"


def test_a_link_to_an_unknown_board_stops_the_import(tmp_path):
    with pytest.raises(CommandError, match="platform_not_allowed"):
        call_command(
            "import_external_vacancies",
            file=write(tmp_path, [entry(external_url="https://example.com/jobs/1")]),
            verbosity=0,
        )
    assert ExternalVacancy.objects.count() == 0


def test_a_source_that_disagrees_with_the_link_stops_the_import(tmp_path):
    """Otherwise a card offers to open a cloz.uz posting "on OLX"."""
    with pytest.raises(CommandError, match="source says"):
        call_command(
            "import_external_vacancies",
            file=write(tmp_path, [entry(source="OLX",
                                        external_url="https://cloz.uz/vacancies/kuryer-1")]),
            verbosity=0,
        )


def test_the_same_link_twice_in_one_file_stops_the_import(tmp_path):
    with pytest.raises(CommandError, match="listed twice"):
        call_command(
            "import_external_vacancies",
            file=write(tmp_path, [entry(), entry(title="Тот же")]),
            verbosity=0,
        )


def test_a_listing_without_a_date_is_refused(tmp_path):
    """A card with no date cannot tell a reader how stale it is."""
    with pytest.raises(CommandError, match="collected_on"):
        call_command(
            "import_external_vacancies",
            file=write(tmp_path, [entry(collected_on="")]),
            verbosity=0,
        )


def test_nothing_is_written_when_one_entry_is_bad(tmp_path):
    """The whole file is one transaction, so a typo halfway down does not
    leave the list half imported."""
    rows = [entry(), entry(external_url="https://example.com/x", title="Плохая")]
    with pytest.raises(CommandError):
        call_command("import_external_vacancies", file=write(tmp_path, rows), verbosity=0)

    assert ExternalVacancy.objects.count() == 0


def test_missing_listings_stay_visible_unless_asked_otherwise(tmp_path):
    call_command("import_external_vacancies", file=write(tmp_path, [entry()]), verbosity=0)

    call_command(
        "import_external_vacancies",
        file=write(tmp_path, [entry(external_url="https://t.me/unilance/1",
                                    source="TELEGRAM", title="Другая")]),
        verbosity=0,
    )

    assert ExternalVacancy.objects.filter(is_active=True).count() == 2


def test_they_can_be_retired_on_request(tmp_path):
    call_command("import_external_vacancies", file=write(tmp_path, [entry()]), verbosity=0)

    call_command(
        "import_external_vacancies",
        file=write(tmp_path, [entry(external_url="https://t.me/unilance/1",
                                    source="TELEGRAM", title="Другая")]),
        deactivate_missing=True,
        verbosity=0,
    )

    assert ExternalVacancy.objects.filter(is_active=True).count() == 1


# ---------------------------------------------------------------------------
# The file that ships with the repository
# ---------------------------------------------------------------------------
def test_the_shipped_list_imports():
    call_command("import_external_vacancies", verbosity=0)

    assert ExternalVacancy.objects.count() > 0


def test_every_shipped_link_passes_the_rules():
    rows = json.loads(DATA.read_text(encoding="utf-8"))

    for row in rows:
        url = clean_vacancy_url(row["external_url"])
        assert source_for(url) == row["source"], row["title"]


def test_the_shipped_list_has_no_repeats():
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    urls = [row["external_url"] for row in rows]

    assert len(urls) == len(set(urls))


def test_every_shipped_listing_can_be_read_by_a_person():
    """A card with no title or no date is one nobody can judge."""
    rows = json.loads(DATA.read_text(encoding="utf-8"))

    for row in rows:
        assert row["title"].strip(), row["external_url"]
        assert row["collected_on"], row["title"]
        assert row["fit"] in ExternalFit.values, row["title"]


# ---------------------------------------------------------------------------
# What the student is served
# ---------------------------------------------------------------------------
def test_the_list_needs_a_signed_in_caller(api):
    assert api.get(URL).status_code == 401


def test_a_student_sees_the_listings(auth, student, tmp_path):
    call_command("import_external_vacancies", file=write(tmp_path, [entry()]), verbosity=0)

    response = auth(student).get(URL)

    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["external_url"].startswith("https://")


def test_a_retired_listing_is_not_served(auth, student, tmp_path):
    call_command("import_external_vacancies", file=write(tmp_path, [entry()]), verbosity=0)
    ExternalVacancy.objects.update(is_active=False)

    assert auth(student).get(URL).data["count"] == 0


def test_the_card_carries_the_name_of_the_board(auth, student, tmp_path):
    """The button says it — "Open on OLX" — so the client is given the word
    rather than keeping its own copy of the mapping."""
    call_command("import_external_vacancies", file=write(tmp_path, [entry()]), verbosity=0)

    assert auth(student).get(URL).data["results"][0]["source_label"] == "OLX"


def test_the_list_can_be_narrowed_to_the_ones_without_a_catch(auth, student, tmp_path):
    rows = [entry(), entry(external_url="https://t.me/unilance/1", source="TELEGRAM",
                           title="С оговорками", fit="CAVEATS")]
    call_command("import_external_vacancies", file=write(tmp_path, rows), verbosity=0)

    assert auth(student).get(f"{URL}?fit=FITS").data["count"] == 1


def test_the_ones_without_a_catch_come_first(auth, student, tmp_path):
    rows = [entry(external_url="https://t.me/unilance/1", source="TELEGRAM",
                  title="С оговорками", fit="CAVEATS"), entry(title="Подходит")]
    call_command("import_external_vacancies", file=write(tmp_path, rows), verbosity=0)

    results = auth(student).get(URL).data["results"]

    assert results[0]["fit"] == "FITS"


def test_the_listings_are_read_only(auth, student):
    response = auth(student).post(URL, entry(), format="json")

    assert response.status_code in (403, 405)
