from django.core.management.base import BaseCommand

from apps.nutrition import off_refresh_worker


class Command(BaseCommand):
    """The background half of apps.nutrition.off_refresh_worker -
    started by FoodAdmin's "Refresh selected foods" actions. Safe to
    run by hand too (e.g. after a restart left foods queued): it exits
    at once if another worker is already running."""

    help = "Refresh the foods queued for an Open Food Facts / Open Prices refresh."

    def handle(self, *args, **options):
        attempted = off_refresh_worker.run()
        self.stdout.write(f"Open Food Facts refresh queue: {attempted} food(s) refreshed.")
