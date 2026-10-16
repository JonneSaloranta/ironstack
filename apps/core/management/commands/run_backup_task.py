from django.core.management.base import BaseCommand

from apps.core import backup_tasks


class Command(BaseCommand):
    """The background half of apps.core.backup_tasks — started by the
    Backups page, not meant to be run by hand (use `create_backup` for
    that). The outcome goes to the task status file, not the exit code."""

    help = "Run one backup task started from the web UI (internal)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=backup_tasks.ACTIONS)
        parser.add_argument("name", nargs="?")

    def handle(self, *args, action, name, **options):
        status = backup_tasks.run(action, name)
        self.stdout.write(f"Backup task {action} {name or ''}: {status['state']}")
