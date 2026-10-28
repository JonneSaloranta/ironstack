from django.core.management.base import BaseCommand, CommandError

from apps.api import schema


class Command(BaseCommand):
    help = (
        "Write each API version's OpenAPI schema to docs/api/, or with --check "
        "fail if a committed schema no longer matches the code."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true", help="Only report outdated schemas; write nothing."
        )

    def handle(self, *args, check=False, **options):
        if check:
            outdated = schema.outdated_versions()
            if outdated:
                raise CommandError(
                    f"API schema out of date for {', '.join(outdated)} - run "
                    "`python manage.py api_schema` and review the diff (docs/API.md "
                    '"Versioning").'
                )
            self.stdout.write("API schemas are up to date.")
            return
        for version in schema.VERSIONS:
            path = schema.schema_path(version)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(schema.build_schema(version))
            self.stdout.write(f"Wrote {path}")
