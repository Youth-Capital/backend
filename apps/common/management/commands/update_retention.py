"""Update 30/90/180-day retention flags on placements.

Retention is the difference between "we placed people" and "we placed people
who stayed" — TZ §15 asks for the second. Run daily.
"""

from django.core.management.base import BaseCommand

from apps.jobs.services import refresh_retention_flags


class Command(BaseCommand):
    help = "Refresh retention_30 / retention_90 / retention_180 on placements."

    def handle(self, *args, **options):
        updated = refresh_retention_flags()
        self.stdout.write(
            self.style.SUCCESS(f"Updated retention flags on {updated} placements.")
        )
