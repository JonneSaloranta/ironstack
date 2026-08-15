from django.core.management.base import BaseCommand

from apps.core import backups as backup_services


class Command(BaseCommand):
    """A CLI entry point for apps.core.backups.create_backup() — the
    same backup the profile page's "Create backup" button triggers,
    usable from a cron job or a scheduled container
    (docker-compose.yml's `backup-scheduler` service) without needing
    an authenticated web request. See docs/BACKUP.md."""

    help = "Create a full backup archive (database + media + manifest) in the backups volume."

    def add_arguments(self, parser):
        # --source is deliberately undocumented in --help's everyday
        # usage — apps.core.management.commands.backup_scheduler is
        # the only built-in caller that ever passes "scheduled", so
        # its own backups tag distinctly (Profile → Administration →
        # Backups) from ones made by running this command directly
        # (by hand, or from a host cron entry someone set up
        # themselves — see docs/BACKUP.md), which stay "manual".
        parser.add_argument("--source", default="manual")

    def handle(self, *args, **options):
        name = backup_services.create_backup(source=options["source"])
        self.stdout.write(self.style.SUCCESS(f"Backup created: {name}"))
