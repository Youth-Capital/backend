"""Recompute stored CV quality ratings.

The rating is refreshed whenever a CV is edited or opened, but it also depends
on things that change elsewhere — a test passed, a job added, a certificate
issued. Those do not touch the document, so the stored column drifts. This is
the cron job that catches up (nightly is plenty), and the one to run once after
deploying the rating feature so existing CVs stop showing zero.
"""

from django.core.management.base import BaseCommand
from django.db.models import F, Q

from apps.cv.models import CVDocument
from apps.cv.rating import refresh_cv_rating


class Command(BaseCommand):
    help = "Recompute the quality rating of every CV document."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--only-stale",
            action="store_true",
            help="Skip CVs whose rating was computed after their last edit.",
        )
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options) -> None:
        queryset = CVDocument.objects.select_related(
            "user", "user__student_profile", "target_profession"
        ).order_by("-updated_at")

        if options["only_stale"]:
            queryset = queryset.filter(
                Q(quality_computed_at__isnull=True)
                | Q(quality_computed_at__lt=F("updated_at"))
            )
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        processed, failed = 0, 0
        for cv in queryset.iterator(chunk_size=100):
            try:
                refresh_cv_rating(cv)
                processed += 1
            except Exception as exc:  # pragma: no cover - one bad row must not
                failed += 1  # stop the batch
                self.stderr.write(f"  ! {cv.id}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(f"Rated {processed} CV(s); {failed} failed.")
        )
