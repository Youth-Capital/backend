"""Load the reviewed list of outside job postings.

The file next to this command is the list, and the list is the thing somebody
maintains: a board's posting expires in days, so the sensible loop is to edit
the JSON and run this again rather than to type rows into an admin form.

Idempotent by address. The URL is the identity of a posting — the same job
posted twice is the same job — so a second run updates what changed and adds
what is new, and never produces a second card for a link already on the list.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.jobs.external import clean_vacancy_url, source_for
from apps.jobs.models import ExternalFit, ExternalVacancy, VacancySource

DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "data" / "external_vacancies.json"

FIELDS = (
    "source",
    "title",
    "company",
    "city",
    "schedule",
    "pay",
    "fit",
    "note",
    "collected_on",
)


class Command(BaseCommand):
    help = "Import or refresh the reviewed list of vacancies hosted elsewhere."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=str(DEFAULT_SOURCE),
            help="JSON list to read. Defaults to the one kept beside this command.",
        )
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help=(
                "Hide listings that are no longer in the file instead of leaving "
                "them visible. Off by default: a shortened file is usually an "
                "edit in progress, not a decision to retire everything else."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"No such file: {path}")

        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CommandError(f"{path} is not valid JSON: {error}") from error
        if not isinstance(rows, list):
            raise CommandError(f"{path} must hold a list of listings.")

        seen: set[str] = set()
        created = updated = 0

        for index, row in enumerate(rows, start=1):
            where = f"entry {index}"
            if not isinstance(row, dict):
                raise CommandError(f"{where}: expected an object.")

            title = (row.get("title") or "").strip()
            if not title:
                raise CommandError(f"{where}: a listing needs a title.")

            # The address decides both identity and whether the entry is
            # allowed at all, so it is cleaned before anything else is read.
            try:
                url = clean_vacancy_url(row.get("external_url", ""))
            except ValueError as error:
                raise CommandError(f"{where} ({title}): {error}") from error
            if url in seen:
                raise CommandError(f"{where} ({title}): {url} is listed twice.")
            seen.add(url)

            # The source is declared in the file and derived from the host; if
            # the two disagree the file is wrong, and a card would otherwise
            # offer to open a cloz.uz posting "on OLX".
            derived = source_for(url)
            declared = (row.get("source") or "").strip().upper()
            if declared and declared != derived:
                raise CommandError(
                    f"{where} ({title}): source says {declared}, the link is {derived}."
                )
            if derived not in VacancySource.values:
                raise CommandError(f"{where} ({title}): unknown source {derived!r}.")

            fit = (row.get("fit") or ExternalFit.FITS).strip().upper()
            if fit not in ExternalFit.values:
                raise CommandError(f"{where} ({title}): unknown fit {fit!r}.")

            if not row.get("collected_on"):
                raise CommandError(
                    f"{where} ({title}): collected_on is required — a card with no "
                    "date cannot tell a reader how stale it is."
                )

            defaults = {field: row.get(field, "") or "" for field in FIELDS}
            defaults.update(
                source=derived,
                fit=fit,
                collected_on=row["collected_on"],
                is_active=bool(row.get("is_active", True)),
            )

            _, was_created = ExternalVacancy.objects.update_or_create(
                external_url=url, defaults=defaults
            )
            created += was_created
            updated += not was_created

        hidden = 0
        if options["deactivate_missing"]:
            hidden = (
                ExternalVacancy.objects.filter(is_active=True)
                .exclude(external_url__in=seen)
                .update(is_active=False)
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"External vacancies: {created} added, {updated} refreshed, "
                f"{hidden} hidden, {ExternalVacancy.objects.filter(is_active=True).count()} "
                "live."
            )
        )
