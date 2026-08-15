import time
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import BackupSettings


def _seconds_until(hour):
    now = timezone.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


class Command(BaseCommand):
    """Runs `create_backup` once a day, forever — docker-compose.yml's
    `backup-scheduler` service's only job (docs/BACKUP.md). Deliberately
    not a real cron daemon (no `cron` package added to the image just
    for this): a plain sleep-until-target-hour loop is enough for "run
    automatically, roughly daily" without relying on the host's own
    cron for a self-contained Docker Compose stack. If precise
    wall-clock timing matters more than that, point a real host cron
    entry at `docker compose exec web python manage.py create_backup`
    instead and drop this service.

    Reads BackupSettings.load() fresh on every wake-up, not just once
    at process startup — so changing the hour or toggling "enabled" on
    Profile → Administration → Backups takes effect without restarting
    this container. A settings change made *while* the loop is asleep
    only takes effect from the *next* wake-up onward, since a sleep
    already in progress isn't interrupted early.

    A single failed backup attempt (e.g. the database briefly
    unreachable) is logged and the loop keeps going rather than the
    whole scheduler process dying and silently stopping all future
    backups until someone notices and restarts it by hand.
    """

    help = "Create a backup once a day at the admin-configured hour (UTC), forever."

    def handle(self, *args, **options):
        self.stdout.write("Backup scheduler started.")
        while True:
            backup_settings = BackupSettings.load()
            seconds = _seconds_until(backup_settings.hour)
            self.stdout.write(
                f"Next check in {seconds // 3600}h {(seconds % 3600) // 60}min "
                f"(daily at {backup_settings.hour:02d}:00 UTC)."
            )
            time.sleep(seconds)

            backup_settings = BackupSettings.load()  # may have changed while asleep
            if not backup_settings.enabled:
                self.stdout.write("Automatic backups are disabled — skipping.")
                continue
            try:
                call_command("create_backup")
            except Exception as exc:
                # Deliberately broad: any failure here must never take
                # the scheduler process itself down.
                self.stderr.write(self.style.ERROR(f"Scheduled backup failed: {exc}"))
