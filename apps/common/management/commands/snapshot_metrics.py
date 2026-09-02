"""Persist today's headline metrics.

The manager dashboard slices by region, education status and cohort; running
those aggregations live over the event table gets slower every week of the
programme, so they are precomputed once a day.
"""

from django.core.management.base import BaseCommand

from apps.analytics.services import snapshot_daily_metrics


class Command(BaseCommand):
    help = "Write today's DailyMetric rows."

    def handle(self, *args, **options):
        written = snapshot_daily_metrics()
        self.stdout.write(self.style.SUCCESS(f"Wrote {written} daily metrics."))
