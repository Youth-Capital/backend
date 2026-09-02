"""Import a partner's course catalogue from a JSON file.

    python manage.py import_courses --provider acme --file catalogue.json

Safe to re-run: courses are keyed on the partner's own external_id, so a
corrected batch updates in place instead of duplicating.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ...importers import import_catalogue
from ...providers import ContentProvider


class Command(BaseCommand):
    help = "Import courses delivered by a content partner."

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True, help="Provider slug.")
        parser.add_argument("--file", required=True, help="Path to the JSON catalogue.")
        parser.add_argument(
            "--create-provider",
            action="store_true",
            help="Create the provider if the slug is unknown.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"No such file: {path}")

        slug = options["provider"]
        provider = ContentProvider.objects.filter(slug=slug).first()
        if provider is None:
            if not options["create_provider"]:
                raise CommandError(
                    f"Unknown provider {slug!r}. Re-run with --create-provider to add it."
                )
            provider = ContentProvider.objects.create(slug=slug, name=slug.title())
            self.stdout.write(f"Created provider {provider.name}")

        if not provider.accepts_imports:
            raise CommandError(f"{provider.name} is not accepting imports.")

        payload = json.loads(path.read_text(encoding="utf-8"))
        run = import_catalogue(provider, payload, source=path.name)

        self.stdout.write(
            self.style.SUCCESS(
                f"{run.status}: {run.courses_created} created, "
                f"{run.courses_updated} updated, {run.lessons_written} lessons, "
                f"{run.skipped} skipped."
            )
        )
        for problem in run.errors[:20]:
            self.stdout.write(self.style.WARNING(f"  {problem['course']}: {problem['error']}"))

        # Imported courses wait for moderation — nothing reaches learners
        # because a partner sent a file.
        self.stdout.write("Imported courses are PENDING_REVIEW until approved.")
