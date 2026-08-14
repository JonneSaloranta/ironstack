#!/bin/sh
# Restores a backup created by scripts/backup.sh. DESTRUCTIVE: replaces
# the running database and every file under media/ entirely. Always
# requires typing "yes" at the prompt below — there is no flag to skip
# it. See docs/BACKUP.md.
set -eu

cd "$(dirname "$0")/.."

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "Usage: $0 <path-to-backup.tar.gz>" >&2
  exit 1
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
tar -xzf "$ARCHIVE" -C "$WORKDIR"

if [ ! -f "$WORKDIR/manifest.json" ] || [ ! -f "$WORKDIR/database.dump" ]; then
  echo "$ARCHIVE doesn't look like an IronStack backup (missing manifest.json/database.dump)." >&2
  exit 1
fi

echo "=== Backup manifest ($ARCHIVE) ==="
cat "$WORKDIR/manifest.json"
echo
echo "=== Running instance ==="
docker compose exec -T web python manage.py version_info --pretty < /dev/null
echo

echo "This will PERMANENTLY REPLACE the current database and every file"
echo "under media/ with the contents of $ARCHIVE. This cannot be undone"
echo "(back up the current state first with scripts/backup.sh if unsure)."
printf "Type 'yes' to continue: "
read -r CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Aborted — nothing was changed."
  exit 1
fi

echo "Stopping web (releases its database connections)..."
docker compose stop web

echo "Restoring database..."
docker compose exec -T db sh -c \
  'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"' \
  < /dev/null
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' \
  < "$WORKDIR/database.dump"

echo "Restoring media..."
docker compose run --rm --no-deps -T web sh -c 'rm -rf /app/media/* /app/media/.[!.]* 2>/dev/null; true' \
  < /dev/null
docker compose run --rm --no-deps -T web tar -xf - -C /app/media < "$WORKDIR/media.tar"

echo "Applying migrations (in case this backup predates the running code)..."
docker compose run --rm --no-deps -T web python manage.py migrate --noinput < /dev/null

echo "Starting web..."
docker compose up -d web

echo
echo "Restore complete."
