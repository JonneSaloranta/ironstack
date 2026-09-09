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

# Same BACKUP_ENCRYPTION_KEY .env's Python-side counterpart
# (apps.core.backups, docs/BACKUP.md "Encryption") reads — one setting
# governs encryption for both backup mechanisms, even though this
# script uses openssl rather than that module's own Fernet: a
# self-hoster only ever has to think about "is BACKUP_ENCRYPTION_KEY
# set", not two separate secrets for what's conceptually one feature.
# Read directly from .env (not `docker compose exec`'s env_file
# handling) since this script's own encryption step runs on the host,
# never inside a container. Empty/unset (the default) leaves this
# archive exactly as before this feature existed — plain
# database.dump/media.tar members.
BACKUP_ENCRYPTION_KEY="$(grep -m1 '^BACKUP_ENCRYPTION_KEY=' .env 2>/dev/null | cut -d= -f2- || true)"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Dumping database..."
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$WORKDIR/database.dump"

echo "Archiving media..."
docker compose exec -T web tar -cf - -C /app/media . > "$WORKDIR/media.tar"

echo "Writing manifest..."
docker compose exec -T web python manage.py version_info --pretty > "$WORKDIR/manifest.json"

DATABASE_MEMBER="database.dump"
MEDIA_MEMBER="media.tar"
if [ -n "$BACKUP_ENCRYPTION_KEY" ]; then
  echo "Encrypting..."
  # -pass env:VAR, not pass:$BACKUP_ENCRYPTION_KEY — the latter would
  # put the key itself on this process's command line, briefly visible
  # to any other user on the same host via `ps`.
  export BACKUP_ENCRYPTION_KEY
  openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY \
    -in "$WORKDIR/database.dump" -out "$WORKDIR/database.dump.enc"
  rm -f "$WORKDIR/database.dump"
  openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY \
    -in "$WORKDIR/media.tar" -out "$WORKDIR/media.tar.enc"
  rm -f "$WORKDIR/media.tar"
  DATABASE_MEMBER="database.dump.enc"
  MEDIA_MEMBER="media.tar.enc"
fi

mkdir -p backups
ARCHIVE="backups/ironstack-backup-$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$WORKDIR" "$DATABASE_MEMBER" "$MEDIA_MEMBER" manifest.json

echo
echo "Backup written to $ARCHIVE"
ls -lh "$ARCHIVE"
