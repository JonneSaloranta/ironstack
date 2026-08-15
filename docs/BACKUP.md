# Backup & restore

Two independent mechanisms exist, deliberately not sharing storage
(see "Why two mechanisms" below):

- **Host-side scripts** (`scripts/backup.sh`/`restore.sh`) — run from
  the Docker host, writing to `backups/` on the host filesystem. The
  safer of the two: restore stops the `web` service first, so nothing
  is trying to use the database while it's replaced.
- **Web UI** (Profile → Administration → Backups, admin/staff only) —
  create, download, and restore backups without leaving the app,
  stored in the `backups_data` Docker volume. Considerably riskier for
  restore specifically — see that section.

## Host-side scripts

Run on the Docker host (not inside a container) from the repo root,
alongside the already-running `docker compose` stack.

### Backing up

```bash
./scripts/backup.sh
```

Writes `backups/ironstack-backup-<UTC timestamp>.tar.gz`, containing:

- `database.dump` — a `pg_dump` custom-format dump of the whole
  PostgreSQL database (`docker compose exec db pg_dump ...`).
- `media.tar` — everything under the `media` volume (`MEDIA_ROOT`).
  Currently empty for a fresh install — no model has a `FileField`/
  `ImageField` yet — but archived unconditionally so nothing is missed
  the day one is added and someone forgets to update this script.
- `manifest.json` — the running instance's `version_info` output
  (`docs/ARCHITECTURE.md` "Versioning": app version, git commit,
  Django migration state, timestamp), so a later restore can tell
  what it's actually looking at without guessing.

`backups/` is gitignored and lives on the same disk as the running
stack — copy the archive somewhere else (another host, object storage,
whatever your actual disaster-recovery plan is) for it to be a real
backup rather than a second copy that dies with the first.

### Restoring

```bash
./scripts/restore.sh backups/ironstack-backup-<timestamp>.tar.gz
```

**Destructive** — replaces the running database and every file under
`media/` with the archive's contents. There is no flag to skip
confirmation: the script prints the backup's manifest next to the
running instance's own `version_info`, then requires typing `yes`
before touching anything. If in doubt, run `scripts/backup.sh` first
to capture the current state before restoring an older one over it.

What it does, in order:
1. Shows the backup's manifest and the running instance's own
   `version_info` side by side, and asks for confirmation.
2. Stops the `web` service — releases its database connections, which
   Postgres otherwise refuses to drop a database while held.
3. Drops and recreates the database, then `pg_restore`s the dump into
   it.
4. Replaces `media/`'s contents with the archive's.
5. Runs `manage.py migrate` — if the backup predates the currently
   running code, this brings its schema forward to match, the normal
   Django migration path. Restoring a *newer* backup into *older* code
   is not supported or specially detected; upgrade the running code
   first if that's ever the situation.
6. Starts `web` back up.

## Web UI

Profile → Administration → Backups (`apps.core.backups`,
`apps.core.views_backup`; `is_staff` required, same gate as `/admin/`
and the rest of that page's "danger zone"). "Create backup" writes
the same three files (`database.dump`/`media.tar`/`manifest.json`)
into one `.tar.gz`, listed with a size/timestamp and "Download"/
"Restore" next to each.

**Restore here is riskier than the script's version, on purpose
accepted rather than avoided** — asked explicitly and confirmed before
building it. The request handling the restore is itself running
inside the same `web` service whose database connection is about to
be replaced, so unlike the host script, `web` can't be stopped first
without stopping the very request performing the restore:

- The admin's own session lives in the database being replaced —
  restoring an older backup that predates their current session can
  log them out mid-operation. Expected; log back in afterward.
- A brief window of errors for any *other* concurrent request is
  possible while the swap happens.
- Unlike the host script, this doesn't (and structurally can't the
  same way) stop `web` first — see `apps.core.backups.restore_backup`'s
  own docstring for exactly how it stays safe anyway: it restores into
  a freshly created, differently-named database first, and only swaps
  it in for the live one (`ALTER DATABASE ... RENAME`, with
  `pg_terminate_backend` to force out any lingering connections first)
  once the new data has actually loaded successfully. A failed restore
  — a corrupt archive, a client/server version mismatch, anything —
  therefore never touches the live database at all, unlike a naive
  drop-then-restore-in-place (which is exactly what an earlier version
  of this feature did, and which left the live database completely
  empty the first time a restore actually failed partway through).

The confirmation page shows the backup's manifest next to the running
instance's own, and the actual restore button additionally requires a
JS `confirm()` dialog on top of the page itself — the same "load-bearing
belt and suspenders" pattern other destructive actions in this app use,
just doubled given how much more severe this one is.

pg_dump/pg_restore run inside the `web` container connect to `db` over
the network (password auth), unlike the host script's local-socket
`docker compose exec` — Debian's default `postgresql-client` package
tracks whatever major version Debian currently ships (17 at the time
of writing), a mismatch from `db`'s pinned `postgres:16-alpine` that
broke `pg_restore` outright ("unrecognized configuration parameter
transaction_timeout"); the `Dockerfile` installs `postgresql-client-16`
specifically from the official PostgreSQL apt repository instead. Keep
that pin and `docker-compose.yml`'s `postgres:16-alpine` in sync if the
server's major version is ever upgraded.

## Automatic backups

Both mechanisms above are manual-trigger only. `docker-compose.yml`'s
`backup-scheduler` service runs `manage.py create_backup` (the same
CLI command a host cron entry could call directly — see below) once a
day at `BACKUP_HOUR` (`.env.example`, default `03:00` UTC), forever,
into the web-UI's `backups_data` volume — so a fresh install actually
has a real disaster-recovery story instead of relying on someone
remembering to click a button. On by default in production
(`docker-compose.yml` alone); disabled in local dev
(`docker-compose.override.yml` puts it behind a `manual` Compose
profile that plain `docker compose up` doesn't activate — start it
explicitly with `docker compose --profile manual up backup-scheduler`
if you want to test it locally).

A single failed backup attempt is logged (`docker compose logs
backup-scheduler`) and the scheduler keeps running rather than the
whole process dying and silently stopping every future backup until
someone notices.

Not a real cron daemon (no `cron` package added to the image just for
this) — a plain sleep-until-target-hour loop
(`apps.core.management.commands.backup_scheduler`) is enough for "runs
automatically, roughly daily" without needing the host's own cron for
what's otherwise a self-contained Docker Compose stack. If precise
wall-clock timing matters more than that, remove the `backup-scheduler`
service and point a real host cron entry at `docker compose exec web
python manage.py create_backup` instead — same command, same
`apps.core.backups.create_backup()` and `backups_data` volume the web
UI's own "Create backup" button uses (see "Why two mechanisms" below
for why `scripts/backup.sh` is a genuinely separate path rather than
also calling this).

## Why two mechanisms

The host script's `docker compose exec db pg_dump ...` (a local Unix
socket, trust-authenticated) isn't reachable from inside a *different*
container — `web` has no docker socket access (mounting one in would
be a real security anti-pattern, handing the web app root-equivalent
access to the host), so the web-UI version necessarily works
differently (network connection, password auth, its own storage
volume) rather than sharing code with the host script. Both mechanisms
produce the same archive format (`database.dump`/`media.tar`/
`manifest.json` in one `.tar.gz`), just stored and triggered
differently — a backup made one way can't currently be listed or
restored from the other's UI/CLI, only manually moved between
`backups/` (host) and the `backups_data` volume (`docker cp`, or by
mounting the volume somewhere and copying).

## What isn't covered

- `.env` (secrets, `DJANGO_ALLOWED_HOSTS`, etc.) — not data, and
  shouldn't travel with a database backup; keep it under your own
  secrets management instead.
- Static files (`staticfiles/`) — fully regenerated by
  `manage.py collectstatic`, already run automatically on every
  container start (`docker-compose.yml`), never worth backing up.
