"""Build/release metadata — the single source of truth future
backup/restore tooling should read instead of re-deriving any of this
independently (see `docs/ARCHITECTURE.md` "Versioning" and the
`version_info` management command, which bundles everything below into
one JSON blob).

- `get_version()` reads the plain-text `VERSION` file at the repo root
  rather than a hardcoded constant here or something derived from git,
  so the exact same value is trivially readable by non-Python tooling
  too (a shell script can just `cat VERSION`). `COPY . .` in the
  Dockerfile bakes it into the image the same way the application code
  itself is baked in, so bumping a release means editing one file and
  rebuilding — no migration, no settings change.
- `get_git_sha()` reads a `GIT_SHA` file the same way, but that file is
  never committed (it's a build artifact, not source — see
  `scripts/build.sh`) and only exists in an image actually built by
  that script; a plain `docker compose up -d --build` (or any dev
  container) has no such file, and this reports "unknown" rather than
  failing. Distinct from `VERSION` on purpose: two builds can share a
  version number (e.g. a hotfix before the next bump), but never share
  a commit.
- `get_migration_state()` is the real technical compatibility signal
  for whether a database backup is safe to load into a given
  code version — a `VERSION` string alone can't answer that (the same
  version could theoretically span a migration during development, or
  two different versions could share a migration state). Reflects live
  database state, so unlike the two functions above it is never cached.
- `get_static_assets_hash()` is apps.core.views.service_worker's own
  cache-busting signal for static/sw.js's `STATIC_CACHE` name — see
  that view's own docstring for why `get_git_sha()` alone doesn't
  cover this: it only ever changes on a real deploy (a build baking in
  a new commit), but a *dev* container (docker-compose.override.yml)
  never gets one at all (GIT_SHA stays "unknown" all container-
  lifetime), which is exactly the case that bit this project once
  already — a browser tab kept open through an entire live-editing
  session stayed on a stale cached static/js/month-calendar.js through
  several real in-place rewrites of that exact file, each only fixed
  by a hard reload discarding the cached copy by hand. Hashing the
  actual current bytes of every CSS/JS file the service worker's own
  fetch handler would cache means the value changes the instant any of
  them genuinely does, dev live-edit or real deploy alike — the one
  mechanism covers both instead of needing a separate path for each.

`get_version()`/`get_git_sha()` are read once and cached — neither
file changes without a container restart anyway.
`get_static_assets_hash()` deliberately isn't: the files it reads can
change without one (a dev container's `static/` is bind-mounted,
docker-compose.override.yml), so caching it would reintroduce the
exact staleness this function exists to prevent.
"""

import hashlib
from functools import lru_cache

from django.conf import settings
from django.db import connection


@lru_cache(maxsize=1)
def get_version():
    try:
        return (settings.BASE_DIR / "VERSION").read_text().strip()
    except FileNotFoundError:
        return "unknown"


@lru_cache(maxsize=1)
def get_git_sha():
    try:
        return (settings.BASE_DIR / "GIT_SHA").read_text().strip()
    except FileNotFoundError:
        return "unknown"


def get_static_assets_hash():
    digest = hashlib.sha256()
    for subdir in ("css", "js"):
        directory = settings.BASE_DIR / "static" / subdir
        for path in sorted(directory.glob("*")):
            if path.is_file():
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def get_migration_state():
    """`{app_label: latest_applied_migration_name}` for every app with
    at least one applied migration, ordered so the most recently
    applied migration per app wins. Returns `{}` before the first
    `migrate` has ever run (the migration-recorder table itself
    doesn't exist yet) rather than raising."""
    from django.db.migrations.recorder import MigrationRecorder

    recorder = MigrationRecorder(connection)
    if not recorder.has_table():
        return {}
    latest = {}
    rows = recorder.migration_qs.order_by("applied").values_list("app", "name")
    for app, name in rows:
        latest[app] = name
    return latest
