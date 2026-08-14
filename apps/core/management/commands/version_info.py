import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.version import get_git_sha, get_migration_state, get_version


class Command(BaseCommand):
    """Prints IronStack's running version/build/migration metadata as
    one JSON blob — the intended hook for a future backup script to
    call (e.g. `docker compose exec web python manage.py version_info`)
    to stamp an archive, and for a future restore path to compare
    against the instance it's restoring into, rather than either script
    re-deriving any of this on its own. See apps.core.version and
    docs/ARCHITECTURE.md "Versioning"."""

    help = "Print version/git commit/migration-state metadata as JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pretty", action="store_true", help="Indent the JSON output for human reading."
        )

    def handle(self, *args, **options):
        data = {
            "version": get_version(),
            "git_sha": get_git_sha(),
            "migrations": get_migration_state(),
            "generated_at": timezone.now().isoformat(),
        }
        indent = 2 if options["pretty"] else None
        self.stdout.write(json.dumps(data, indent=indent))
