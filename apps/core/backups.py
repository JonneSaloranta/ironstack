"""Web-UI-triggered backup creation/listing/download/restore — the
profile page's admin-only "Backups" section (apps.core.views). A
parallel mechanism to scripts/backup.sh/restore.sh, deliberately not
sharing storage with them: those write to `backups/` on the Docker
*host* via `docker compose exec`'s local socket access to `db`, which
isn't available from inside a running container. This module instead
shells out to `pg_dump`/`pg_restore`/`dropdb`/`createdb`/`psql`
(installed in the image — see the Dockerfile's `postgresql-client-16`
package, version-pinned to match `db`'s `postgres:16-alpine`) over the
network, the same way Django's own ORM connects to `db`, and stores
archives in the `backups_data` volume (`/app/backups`, mounted only in
`web`). See docs/BACKUP.md for the full picture, including why restore
here is considerably riskier than the host-side script's version.
"""

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.utils import timezone

from apps.core.version import get_git_sha, get_migration_state, get_version

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/backups"))


class InvalidBackupName(Exception):
    pass


def _db_config():
    return settings.DATABASES["default"]


def _pg_env():
    env = os.environ.copy()
    env["PGPASSWORD"] = _db_config()["PASSWORD"]
    return env


def _pg_connection_args():
    db = _db_config()
    return ["-h", db["HOST"], "-p", str(db["PORT"]), "-U", db["USER"]]


def safe_archive_path(name):
    """Resolves `name` (a URL path segment, so attacker-controlled) to a
    path strictly inside BACKUP_DIR — rejects anything containing a
    path separator so `../../etc/passwd`-style traversal can't escape
    the backups directory."""
    if "/" in name or "\\" in name or name in (".", ".."):
        raise InvalidBackupName(name)
    path = BACKUP_DIR / name
    if path.parent != BACKUP_DIR or not path.is_file():
        raise InvalidBackupName(name)
    return path


def list_backups():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for path in sorted(BACKUP_DIR.glob("ironstack-backup-*.tar.gz"), reverse=True):
        stat = path.stat()
        backups.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "created_at": timezone.datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.get_current_timezone()
                ),
            }
        )
    return backups


def create_backup():
    """Dumps the database, archives media/, and writes a version_info
    manifest, bundled into one `ironstack-backup-<timestamp>.tar.gz` in
    BACKUP_DIR. Returns the archive's filename."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")

    with TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        with open(tmp / "database.dump", "wb") as f:
            subprocess.run(
                ["pg_dump", *_pg_connection_args(), "-Fc", _db_config()["NAME"]],
                stdout=f,
                check=True,
                env=_pg_env(),
            )

        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tmp / "media.tar", "w") as tar:
            tar.add(media_root, arcname=".")

        manifest = {
            "version": get_version(),
            "git_sha": get_git_sha(),
            "migrations": get_migration_state(),
            "generated_at": timezone.now().isoformat(),
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))

        archive_name = f"ironstack-backup-{stamp}.tar.gz"
        with tarfile.open(BACKUP_DIR / archive_name, "w:gz") as tar:
            for filename in ("database.dump", "media.tar", "manifest.json"):
                tar.add(tmp / filename, arcname=filename)

    return archive_name


def read_manifest(name):
    """The backup's own manifest.json, without extracting the rest of
    the archive — used to show what a backup contains before deciding
    whether to restore it."""
    path = safe_archive_path(name)
    with tarfile.open(path, "r:gz") as tar:
        member = tar.extractfile("manifest.json")
        return json.loads(member.read())


def _psql(maintenance_args, env, sql):
    subprocess.run(
        ["psql", *maintenance_args, "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=True,
        env=env,
    )


def restore_backup(name):
    """DESTRUCTIVE: replaces the database from this backup's dump,
    replaces every file under media/, and runs `migrate` to bring the
    schema forward to whatever the running code expects. See this
    module's own docstring and docs/BACKUP.md for exactly why this is
    riskier than scripts/restore.sh's version — most notably, the
    request handling this call is itself using a database connection
    that's about to be dropped out from under it.

    Restores into a freshly created, differently-named database first,
    rather than dropping the live one up front — regression: an
    earlier version did drop-then-restore-in-place, and a `pg_restore`
    failure partway through (e.g. a client/server version mismatch)
    left the live database completely empty with no way back. Here, a
    failed restore never touches the live database at all; only once
    the new data has loaded successfully does a live database swap
    (Postgres `ALTER DATABASE ... RENAME`) put it in the running
    database's place.
    """
    path = safe_archive_path(name)

    with TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(tmp, filter="data")

        connections.close_all()

        env = _pg_env()
        pg_args = _pg_connection_args()
        db_name = _db_config()["NAME"]
        db_user = _db_config()["USER"]
        # A superuser session connected to the `postgres` maintenance
        # database — required for renaming/dropping `db_name` itself,
        # which Postgres refuses while any session (including this
        # one) is connected *to* it.
        maintenance_args = [*pg_args, "-d", "postgres"]
        restoring_name = f"{db_name}_restoring"
        previous_name = f"{db_name}_previous"

        subprocess.run(
            ["dropdb", *pg_args, "--if-exists", "--force", restoring_name],
            check=True,
            env=env,
        )
        subprocess.run(
            ["createdb", *pg_args, "-O", db_user, restoring_name], check=True, env=env
        )
        with open(tmp / "database.dump", "rb") as f:
            subprocess.run(
                ["pg_restore", *pg_args, "-d", restoring_name, "--no-owner"],
                stdin=f,
                check=True,
                env=env,
            )

        # The new data is fully loaded at this point — only now do we
        # touch the live database.
        connections.close_all()
        _psql(
            maintenance_args,
            env,
            f'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
            f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid();",
        )
        subprocess.run(
            ["dropdb", *pg_args, "--if-exists", "--force", previous_name],
            check=True,
            env=env,
        )
        _psql(maintenance_args, env, f'ALTER DATABASE "{db_name}" RENAME TO "{previous_name}";')
        _psql(
            maintenance_args, env, f'ALTER DATABASE "{restoring_name}" RENAME TO "{db_name}";'
        )
        subprocess.run(
            ["dropdb", *pg_args, "--if-exists", "--force", previous_name],
            check=True,
            env=env,
        )

        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)
        for child in media_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        with tarfile.open(tmp / "media.tar", "r") as tar:
            tar.extractall(media_root, filter="data")

        call_command("migrate", interactive=False, verbosity=0)
