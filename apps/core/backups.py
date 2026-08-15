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

from apps.core.models import BackupSettings
from apps.core.version import get_git_sha, get_migration_state, get_version

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/backups"))


class InvalidBackupName(Exception):
    pass


class InvalidBackupArchive(Exception):
    """Raised by save_uploaded_backup() below for anything that isn't
    a readable .tar.gz containing every member a real backup has —
    checked before ever writing into BACKUP_DIR, so a bad upload
    doesn't clutter the backup list with a file that would only fail
    later, at restore time, instead of right away."""


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


#: Filename prefix create_backup() never uses for anything it writes
#: itself (see its own "-uploaded-" vs. plain "-" naming) — the one
#: reliable signal that a given archive arrived via "Upload backup"
#: rather than being created by this instance, since an uploaded
#: archive's own manifest.json belongs to whatever instance originally
#: made it and has no way to know it's since been uploaded elsewhere.
_UPLOADED_PREFIX = "ironstack-backup-uploaded-"


def _backup_origin(path):
    """(source, version, git_sha) for one backup — source is "uploaded"
    for anything save_uploaded_backup() wrote, otherwise whatever
    create_backup() itself recorded in the archive's own manifest.json
    ("scheduled" from the backup-scheduler service, "manual" from the
    web UI's "Create backup" button or the create_backup management
    command — see create_backup()'s own `source` parameter), falling
    back to "manual" for a backup made before this field existed at
    all, or for one whose manifest can't be read for any reason (a
    corrupted archive shouldn't break the whole list page over just
    its own source tag)."""
    if path.name.startswith(_UPLOADED_PREFIX):
        return "uploaded", None, None
    try:
        with tarfile.open(path, "r:gz") as tar:
            member = tar.extractfile("manifest.json")
            manifest = json.loads(member.read())
    except (tarfile.TarError, KeyError, json.JSONDecodeError, OSError):
        return "manual", None, None
    return manifest.get("source", "manual"), manifest.get("version"), manifest.get("git_sha")


def list_backups():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for path in sorted(BACKUP_DIR.glob("ironstack-backup-*.tar.gz"), reverse=True):
        stat = path.stat()
        source, version, git_sha = _backup_origin(path)
        backups.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "created_at": timezone.datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.get_current_timezone()
                ),
                "source": source,
                "version": version,
                "git_sha": git_sha,
            }
        )
    return backups


def prune_backups(retention_count):
    """Deletes the oldest backups beyond `retention_count`
    (`list_backups()` is already newest-first). `retention_count <= 0`
    means "keep everything" — never prunes. Called automatically from
    `create_backup()` below, so every path that creates a backup (the
    scheduler, the web UI's "Create backup" button, the `create_backup`
    management command) prunes the same way, rather than each caller
    needing to remember to."""
    if retention_count <= 0:
        return
    for backup in list_backups()[retention_count:]:
        (BACKUP_DIR / backup["name"]).unlink(missing_ok=True)


def create_backup(source="manual"):
    """Dumps the database, archives media/, and writes a version_info
    manifest, bundled into one `ironstack-backup-<timestamp>.tar.gz` in
    BACKUP_DIR, then prunes down to BackupSettings.load().retention_count
    (Profile → Administration → Backups). Returns the new archive's
    filename.

    `source` is recorded in the manifest and is purely descriptive —
    apps.core.management.commands.backup_scheduler is the only caller
    that ever passes "scheduled"; the web UI's "Create backup" button
    and the plain `create_backup` management command (e.g. from a host
    cron entry someone set up themselves — see docs/BACKUP.md) both
    leave it at the "manual" default, since there's no way to tell
    those two apart from here anyway. list_backups()/_backup_origin()
    read it back to tag each backup in Profile → Administration →
    Backups."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # Microseconds too, not just down to the second — two backups
    # created within the same second (a fast retry, an admin clicking
    # "Create backup" right after a scheduled one landed) would
    # otherwise share an identical filename and silently overwrite
    # each other instead of both existing.
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S-%f")

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
            "source": source,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))

        archive_name = f"ironstack-backup-{stamp}.tar.gz"
        with tarfile.open(BACKUP_DIR / archive_name, "w:gz") as tar:
            for filename in ("database.dump", "media.tar", "manifest.json"):
                tar.add(tmp / filename, arcname=filename)

    prune_backups(BackupSettings.load().retention_count)
    return archive_name


_UPLOAD_REQUIRED_MEMBERS = {"database.dump", "media.tar", "manifest.json"}


def save_uploaded_backup(uploaded_file):
    """Profile → Administration → Backups' "Upload backup" card
    (apps.core.views_backup.BackupListView) — accepts a .tar.gz
    previously downloaded (from this instance or another one running a
    compatible version) and stores it in BACKUP_DIR under a fresh,
    server-generated name, never the client-supplied filename — the
    same "don't trust anything from the request" reasoning
    safe_archive_path() already applies to a restore/download target
    name. Runs through the exact same restore path afterward
    (views_backup.BackupRestoreView) as a backup this instance created
    itself — upload is just a second way to get a valid archive into
    BACKUP_DIR, nothing about actually restoring one is different.
    Also prunes down to the retention setting, same as create_backup()
    — an upload counts as a backup existing here now, same as one."""
    try:
        with tarfile.open(fileobj=uploaded_file, mode="r:gz") as tar:
            missing = _UPLOAD_REQUIRED_MEMBERS - set(tar.getnames())
            if missing:
                raise InvalidBackupArchive(f"missing {', '.join(sorted(missing))}")
    except tarfile.TarError as exc:
        raise InvalidBackupArchive("not a valid .tar.gz archive") from exc

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # Microseconds too — see create_backup()'s own comment on why
    # (two uploads landing in the same wall-clock second would
    # otherwise silently overwrite each other).
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S-%f")
    archive_name = f"ironstack-backup-uploaded-{stamp}.tar.gz"
    uploaded_file.seek(0)
    # copyfileobj rather than Django UploadedFile's own .chunks() —
    # works the same for a real multipart upload (an InMemoryUploadedFile/
    # TemporaryUploadedFile, both real files) and for a plain
    # file-like object (io.BytesIO, e.g. in a test), so this function
    # doesn't need to assume anything Django-specific about its input
    # beyond read()/seek().
    with open(BACKUP_DIR / archive_name, "wb") as dest:
        shutil.copyfileobj(uploaded_file, dest)

    prune_backups(BackupSettings.load().retention_count)
    return archive_name


def read_manifest(name):
    """The backup's own manifest.json, without extracting the rest of
    the archive — used to show what a backup contains before deciding
    whether to restore it."""
    path = safe_archive_path(name)
    with tarfile.open(path, "r:gz") as tar:
        member = tar.extractfile("manifest.json")
        return json.loads(member.read())


def delete_backup(name):
    """Removes one backup from BACKUP_DIR — Profile → Administration →
    Backups' own "Delete" action. Unlike restoring, this is
    non-destructive to anything actually running (it only ever
    discards a copy sitting in storage), so unlike restore_backup()
    below it needs no confirm-page/manifest-comparison ceremony of its
    own beyond the same JS confirm() every other delete in this app
    uses. `safe_archive_path` does the same traversal/existence check
    every other name-based lookup in this module already relies on."""
    path = safe_archive_path(name)
    path.unlink()


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
