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

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.utils import timezone

from apps.core.models import BackupSettings
from apps.core.version import get_git_sha, get_migration_state, get_version

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/backups"))


class InvalidBackupName(Exception):
    pass


class BackupDecryptionError(Exception):
    """Raised by restore_backup() when a backup's manifest says it was
    encrypted but the currently configured BACKUP_ENCRYPTION_KEY
    (settings.BACKUP_ENCRYPTION_KEY, docs/BACKUP.md "Encryption")
    can't actually decrypt it — either unset, or set to a different
    key than the one this specific archive was made with. Distinct
    from InvalidBackupArchive below: the archive itself is perfectly
    valid, this instance just doesn't hold the right key for it."""


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


#: Suffix create_backup() appends to database.dump/media.tar's own
#: names once encrypted (docs/BACKUP.md "Encryption") — manifest.json
#: itself is never encrypted (list_backups()/read_manifest() need to
#: read it without the key), only these two payload members are.
_ENCRYPTED_SUFFIX = ".enc"


def _encrypt_member(path):
    """Encrypts `path` in place with settings.BACKUP_ENCRYPTION_KEY and
    returns the new, `_ENCRYPTED_SUFFIX`-suffixed path — the plaintext
    original is removed, never left sitting next to its own encrypted
    copy on disk. Whole-file-in-memory (Fernet has no streaming mode),
    the same "build it all in a TemporaryDirectory first" scale
    already assumed by create_backup() building database.dump/
    media.tar themselves that way."""
    fernet = Fernet(settings.BACKUP_ENCRYPTION_KEY)
    encrypted_path = path.with_name(path.name + _ENCRYPTED_SUFFIX)
    encrypted_path.write_bytes(fernet.encrypt(path.read_bytes()))
    path.unlink()
    return encrypted_path


def _decrypt_member(path):
    """The other half of _encrypt_member — used by restore_backup()
    below. Raises BackupDecryptionError (not cryptography's own
    InvalidToken) for a missing/wrong key: the callers here are
    request handlers and a management command, not code that should
    ever need to know this module reaches for Fernet specifically."""
    if not settings.BACKUP_ENCRYPTION_KEY:
        raise BackupDecryptionError(
            "This backup is encrypted, but BACKUP_ENCRYPTION_KEY isn't set on this instance."
        )
    try:
        fernet = Fernet(settings.BACKUP_ENCRYPTION_KEY)
        return fernet.decrypt(path.read_bytes())
    except InvalidToken as exc:
        raise BackupDecryptionError(
            "Couldn't decrypt this backup — BACKUP_ENCRYPTION_KEY doesn't match the key "
            "it was encrypted with."
        ) from exc


def _ensure_members_decrypted(tmp):
    """Given `tmp` (an already-extracted backup archive's own
    directory, manifest.json included), decrypts database.dump.enc/
    media.tar.enc back down to plain database.dump/media.tar in place
    when the manifest says this archive is encrypted — a no-op
    otherwise. Everything downstream of this call (restore_backup()
    itself, and every test exercising just this step below) can then
    assume those two plain names exist, unchanged from before backup
    encryption existed at all. Pulled out of restore_backup() as its
    own function specifically so it's testable without also exercising
    that function's real dropdb/pg_restore calls."""
    manifest = json.loads((tmp / "manifest.json").read_text())
    if not manifest.get("encrypted"):
        return
    (tmp / "database.dump").write_bytes(
        _decrypt_member(tmp / ("database.dump" + _ENCRYPTED_SUFFIX))
    )
    (tmp / "media.tar").write_bytes(_decrypt_member(tmp / ("media.tar" + _ENCRYPTED_SUFFIX)))


#: Filename prefix create_backup() never uses for anything it writes
#: itself (see its own "-uploaded-" vs. plain "-" naming) — the one
#: reliable signal that a given archive arrived via "Upload backup"
#: rather than being created by this instance, since an uploaded
#: archive's own manifest.json belongs to whatever instance originally
#: made it and has no way to know it's since been uploaded elsewhere.
_UPLOADED_PREFIX = "ironstack-backup-uploaded-"


def _read_manifest_quietly(path):
    """manifest.json's raw dict, or {} for anything that goes wrong
    reading it — a corrupted/unreadable archive shouldn't break the
    whole list page over just its own metadata, the same reasoning
    _backup_origin's own fallback already used before this was pulled
    out as its own helper."""
    try:
        with tarfile.open(path, "r:gz") as tar:
            member = tar.extractfile("manifest.json")
            return json.loads(member.read())
    except (tarfile.TarError, KeyError, json.JSONDecodeError, OSError):
        return {}


def _backup_origin(path):
    """(source, version, git_sha, encrypted) for one backup — source is
    "uploaded" for anything save_uploaded_backup() wrote, otherwise
    whatever create_backup() itself recorded in the archive's own
    manifest.json ("scheduled" from the backup-scheduler service,
    "manual" from the web UI's "Create backup" button or the
    create_backup management command — see create_backup()'s own
    `source` parameter), falling back to "manual" for a backup made
    before this field existed at all, or for one whose manifest can't
    be read for any reason. `encrypted` is read regardless of source,
    upload included — manifest.json is never itself encrypted
    (docs/BACKUP.md "Encryption"), so there's no "belongs to a
    possibly-incompatible instance" concern reading just this one
    field, unlike version/git_sha below (deliberately *not* read for
    an uploaded archive — that manifest.json belongs to whatever
    instance originally made it, not necessarily one running
    compatible code)."""
    manifest = _read_manifest_quietly(path)
    encrypted = bool(manifest.get("encrypted"))
    if path.name.startswith(_UPLOADED_PREFIX):
        return "uploaded", None, None, encrypted
    source = manifest.get("source", "manual")
    return source, manifest.get("version"), manifest.get("git_sha"), encrypted


def list_backups():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for path in sorted(BACKUP_DIR.glob("ironstack-backup-*.tar.gz"), reverse=True):
        stat = path.stat()
        source, version, git_sha, encrypted = _backup_origin(path)
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
                "encrypted": encrypted,
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

        # Encrypts database.dump/media.tar in place — the two members
        # that hold this instance's actual data — whenever
        # BACKUP_ENCRYPTION_KEY is configured (docs/BACKUP.md
        # "Encryption"). manifest.json is deliberately never encrypted:
        # list_backups()/read_manifest() need to show a backup's
        # version/timestamp/source without the key. Unset (the
        # default) leaves every archive exactly as before this setting
        # existed — plain database.dump/media.tar members.
        database_member = Path("database.dump")
        media_member = Path("media.tar")
        if settings.BACKUP_ENCRYPTION_KEY:
            database_member = _encrypt_member(tmp / database_member).relative_to(tmp)
            media_member = _encrypt_member(tmp / media_member).relative_to(tmp)

        manifest = {
            "version": get_version(),
            "git_sha": get_git_sha(),
            "migrations": get_migration_state(),
            "generated_at": timezone.now().isoformat(),
            "source": source,
            "encrypted": bool(settings.BACKUP_ENCRYPTION_KEY),
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))

        archive_name = f"ironstack-backup-{stamp}.tar.gz"
        with tarfile.open(BACKUP_DIR / archive_name, "w:gz") as tar:
            for filename in (database_member, media_member, Path("manifest.json")):
                tar.add(tmp / filename, arcname=str(filename))

    prune_backups(BackupSettings.load().retention_count)
    return archive_name


#: The two payload members either as create_backup() always wrote them
#: pre-encryption (bare) or as it writes them now when
#: BACKUP_ENCRYPTION_KEY is set (_ENCRYPTED_SUFFIX-suffixed) — a valid
#: upload has exactly one variant of each, manifest.json always plain.
_UPLOAD_REQUIRED_MEMBER_PAIRS = (
    {"database.dump", "database.dump" + _ENCRYPTED_SUFFIX},
    {"media.tar", "media.tar" + _ENCRYPTED_SUFFIX},
)


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
            names = set(tar.getnames())
            if "manifest.json" not in names:
                raise InvalidBackupArchive("missing manifest.json")
            missing_pairs = [
                pair for pair in _UPLOAD_REQUIRED_MEMBER_PAIRS if names.isdisjoint(pair)
            ]
            if missing_pairs:
                wanted = ", ".join(sorted(name for pair in missing_pairs for name in pair))
                raise InvalidBackupArchive(f"missing one of: {wanted}")
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

        # Deliberately before connections.close_all()/any dropdb below:
        # a missing or wrong BACKUP_ENCRYPTION_KEY must abort here,
        # before this function has done anything destructive to the
        # live database.
        _ensure_members_decrypted(tmp)

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
