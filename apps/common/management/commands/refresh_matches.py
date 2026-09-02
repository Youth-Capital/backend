"""Recompute match results that were flagged stale.

Matching is invalidated cheaply (a flag) and recomputed in bulk here, so a
single finished lesson never turns into hundreds of score computations inside
an HTTP request. Run every few minutes from cron or Celery beat.
"""

from django.core.management.base import BaseCommand

from apps.matching.models import MatchResult
from apps.matching.services import refresh_stale_matches


class Command(BaseCommand):
    help = "Recompute stale MatchResult rows."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--all",
            action="store_true",
            help="Mark every result stale first (use after changing weights).",
        )

    def handle(self, *args, **options):
        if options["all"]:
            marked = MatchResult.objects.update(is_stale=True)
            self.stdout.write(f"Marked {marked} results stale.")

        pending = MatchResult.objects.filter(is_stale=True).count()
        refreshed = refresh_stale_matches(batch_size=options["batch_size"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Refreshed {refreshed} of {pending} stale results "
                f"({max(pending - refreshed, 0)} remaining)."
            )
        )
