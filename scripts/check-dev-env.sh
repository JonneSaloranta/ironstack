#!/bin/sh
# Diagnoses/fixes the most common way a host-side (non-Docker, see
# README.md "Without Docker") dev environment silently drifts out of
# sync with what CI actually runs: `requirements/dev.txt` growing a
# new package nobody re-ran `pip install` for, and `pg_dump` missing
# or being the wrong major version, which fails every one of
# apps.core.backups' BackupTests — always the *same* 33 tests, with no
# obvious connection to whatever was actually being worked on. Neither
# failure mode is specific to this repo, which is exactly why nothing
# catches it automatically: `pytest` just reports the failures as if
# they were real regressions. Run this after pulling, or any time a
# fresh `pytest` run fails tests unrelated to whatever you touched.
#
# Safe to re-run any time — installing already-satisfied requirements
# and checking pg_dump's version are both no-ops, never destructive.
set -eu

cd "$(dirname "$0")/.."

# Same major/minor Dockerfile's `FROM python:3.14-slim` and
# docs/DEVELOPMENT.md "Pinned versions & tooling" pin — kept as one
# variable rather than repeated below so bumping the project's own
# pin is a one-line change here too.
EXPECTED_PYTHON="3.14"

if [ -z "${VIRTUAL_ENV:-}" ] && [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

if [ -z "${VIRTUAL_ENV:-}" ]; then
  echo "No active virtualenv, and no .venv/ here to activate automatically." >&2
  echo "Create one first: python${EXPECTED_PYTHON} -m venv .venv && source .venv/bin/activate" >&2
  exit 1
fi

python_version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ "$python_version" != "$EXPECTED_PYTHON" ]; then
  echo "Note: this venv is Python $python_version — the project is pinned to $EXPECTED_PYTHON (Dockerfile, docs/DEVELOPMENT.md)."
  echo "Not a hard failure (things generally still work a version or two off), but if you hit anything"
  echo "version-specific, rebuild the venv: rm -rf .venv && python${EXPECTED_PYTHON} -m venv .venv"
  echo
fi

echo "Syncing installed packages with requirements/dev.txt..."
pip install --quiet -r requirements/dev.txt
echo "  done."

# apps.core.backups (the Profile -> Administration -> Backups web UI,
# not scripts/backup.sh below it) shells out to `pg_dump`/`pg_restore`
# directly from wherever Django itself is running — inside the `web`
# container in Docker, or this very host when running without it.
# Debian's own default `postgresql-client` package (whatever major
# version that happens to be) doesn't necessarily match
# docker-compose.yml's `postgres:16-alpine` server — a mismatch makes
# pg_dump embed session settings the server doesn't recognize (e.g.
# "unrecognized configuration parameter transaction_timeout"), not a
# missing-binary error, so this checks the version explicitly rather
# than just presence. See the Dockerfile's own runtime-stage comment
# for the exact same reasoning, and the PGDG repo commands below,
# which mirror what it does to install postgresql-client-16 for
# Debian/Ubuntu specifically.
echo "Checking for a matching pg_dump (needed for apps.core.backups' BackupTests)..."
if command -v pg_dump >/dev/null 2>&1; then
  installed_version="$(pg_dump --version | grep -oE '[0-9]+' | head -1)"
  if [ "$installed_version" = "16" ]; then
    echo "  OK — pg_dump $(pg_dump --version | grep -oE '[0-9]+\.[0-9]+' | head -1)."
  else
    echo "  pg_dump is present but version $installed_version, not 16 — BackupTests will fail with"
    echo "  something like \"unrecognized configuration parameter\" rather than a clean skip."
    echo "  On Debian/Ubuntu, install postgresql-client-16 specifically from the PGDG repo (matches"
    echo "  Dockerfile's runtime stage exactly):"
    echo "    sudo apt-get install -y curl gnupg"
    echo "    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /usr/share/keyrings/pgdg.gpg"
    echo "    . /etc/os-release"
    echo "    echo \"deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt \${VERSION_CODENAME}-pgdg main\" | sudo tee /etc/apt/sources.list.d/pgdg.list"
    echo "    sudo apt-get update && sudo apt-get install -y postgresql-client-16"
  fi
else
  echo "  pg_dump not found — BackupTests will fail with \"FileNotFoundError: pg_dump\", which"
  echo "  otherwise looks like an unrelated regression. Install postgresql-client-16 (see"
  echo "  docs/BACKUP.md and the Dockerfile's runtime stage for the same PGDG-repo steps):"
  echo "    sudo apt-get install -y curl gnupg"
  echo "    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /usr/share/keyrings/pgdg.gpg"
  echo "    . /etc/os-release"
  echo "    echo \"deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt \${VERSION_CODENAME}-pgdg main\" | sudo tee /etc/apt/sources.list.d/pgdg.list"
  echo "    sudo apt-get update && sudo apt-get install -y postgresql-client-16"
fi

echo
echo "Environment check complete. Running Docker instead sidesteps all of this —"
echo "'docker compose exec web pytest' always runs inside the image, which already has both right."
