#!/usr/bin/env bash
set -euo pipefail

CSI_DB_PATH="${CSI_DB_PATH:-/var/lib/universal-agent/csi/csi.db}"
CSI_BACKUP_DIR="${CSI_BACKUP_DIR:-/var/lib/universal-agent/csi/backups}"
CSI_BACKUP_KEEP_DAYS="${CSI_BACKUP_KEEP_DAYS:-14}"
CSI_BACKUP_BASENAME="${CSI_BACKUP_BASENAME:-csi-db}"

mkdir -p "${CSI_BACKUP_DIR}"

if [[ ! -f "${CSI_DB_PATH}" ]]; then
  echo "CSI_DB_BACKUP_SKIPPED=missing_db"
  echo "CSI_DB_PATH=${CSI_DB_PATH}"
  exit 0
fi

lock_file="${CSI_BACKUP_DIR}/.backup.lock"
exec 9>"${lock_file}"
if ! flock -n 9; then
  echo "CSI_DB_BACKUP_SKIPPED=lock_busy"
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp_sqlite="${CSI_BACKUP_DIR}/${CSI_BACKUP_BASENAME}-${stamp}.sqlite"
final_gz="${tmp_sqlite}.gz"

sqlite3 "${CSI_DB_PATH}" ".timeout 5000" "PRAGMA wal_checkpoint(PASSIVE);" >/dev/null 2>&1 || true
sqlite3 "${CSI_DB_PATH}" ".backup '${tmp_sqlite}'"
gzip -f "${tmp_sqlite}"

find "${CSI_BACKUP_DIR}" -maxdepth 1 -type f -name "${CSI_BACKUP_BASENAME}-*.sqlite.gz" -mtime "+${CSI_BACKUP_KEEP_DAYS}" -print -delete || true

latest_size="$(stat -c %s "${final_gz}" 2>/dev/null || echo 0)"
latest_count="$(find "${CSI_BACKUP_DIR}" -maxdepth 1 -type f -name "${CSI_BACKUP_BASENAME}-*.sqlite.gz" | wc -l | tr -d ' ')"

echo "CSI_DB_BACKUP_OK=1"
echo "CSI_DB_BACKUP_FILE=${final_gz}"
echo "CSI_DB_BACKUP_SIZE=${latest_size}"
echo "CSI_DB_BACKUP_TOTAL_FILES=${latest_count}"
