#!/usr/bin/env bash
# Daily SQLite backup. Copies copytrade.db to backups/ with a timestamp,
# keeps the last 14 days, deletes older.
#
# Install (run on the VPS as the bot's user):
#   chmod +x ~/copytrade/backend/deploy/backup-db.sh
#   crontab -e
#   # add this line, runs daily at 5am UTC:
#   0 5 * * * /home/ubuntu/copytrade/backend/deploy/backup-db.sh

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB="$BACKEND_DIR/copytrade.db"
BACKUP_DIR="$BACKEND_DIR/backups"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB" ]; then
  echo "[backup] db not found at $DB"
  exit 1
fi

STAMP="$(date -u +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/copytrade-$STAMP.db"

# SQLite-safe copy (handles WAL even if bot is writing)
sqlite3 "$DB" ".backup '$DEST'" 2>/dev/null \
  || cp "$DB" "$DEST"  # fallback if sqlite3 cli isn't installed

# Keep last 14 days, delete older
find "$BACKUP_DIR" -name 'copytrade-*.db' -mtime +14 -delete

echo "[backup] $DEST"
