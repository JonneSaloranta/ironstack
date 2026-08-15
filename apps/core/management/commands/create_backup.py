from django.core.management.base import BaseCommand

from apps.core import backups as backup_services


class Command(BaseCommand):
    """A CLI entry point for apps.core.backups.create_backup() — the
    same backup the profile page's "Create backup" button triggers,
    usable from a cron job or a scheduled container
    (docker-compose.yml's `backup-scheduler` service) without needing
    an authenticated web request. See docs/BACKUP.md."""

    help = "Create a full backup archive (database + media + manifest) in the backups volume."

    def handle(self, *args, **options):
        name = backup_services.create_backup()
        self.stdout.write(self.style.SUCCESS(f"Backup created: {name}"))
