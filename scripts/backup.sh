#!/bin/sh
# Full backup: a PostgreSQL dump, the media volume's contents, and a
# version_info manifest (docs/ARCHITECTURE.md "Versioning") — bundled
# into one timestamped archive under backups/ on the host, not a
# Docker volume, so it survives even if every container/volume is
# destroyed. See docs/BACKUP.md; scripts/restore.sh is the destructive
# counterpart to this script.
set -eu

cd "$(dirname "$0")/.."

if ! docker compose ps --status running --services 2>/dev/null | grep -qx web; then
  echo "The 'web' service isn't running — start the stack first (docker compose up -d)." >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%d-%H%M%S)"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Dumping database..."
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$WORKDIR/database.dump"

echo "Archiving media..."
docker compose exec -T web tar -cf - -C /app/media . > "$WORKDIR/media.tar"

echo "Writing manifest..."
docker compose exec -T web python manage.py version_info --pretty > "$WORKDIR/manifest.json"

mkdir -p backups
ARCHIVE="backups/ironstack-backup-$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$WORKDIR" database.dump media.tar manifest.json

echo
echo "Backup written to $ARCHIVE"
ls -lh "$ARCHIVE"
