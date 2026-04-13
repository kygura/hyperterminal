#!/usr/bin/env bash
set -euo pipefail

SOURCE_DB="${SOURCE_DB:-/srv/hypertrade/data/data.db}"
BACKUP_ROOT="${BACKUP_ROOT:-/srv/hypertrade/backups}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

daily_dir="${BACKUP_ROOT}/daily"
weekly_dir="${BACKUP_ROOT}/weekly"
date_stamp="$(date -u +%F)"
week_stamp="$(date -u +%G-%V)"
tmp_db="${BACKUP_ROOT}/.hypertrade-${date_stamp}.sqlite"

mkdir -p "${daily_dir}" "${weekly_dir}"
rm -f "${tmp_db}" "${tmp_db}.gz"

"${PYTHON_BIN}" - "${SOURCE_DB}" "${tmp_db}" <<'PY'
import sqlite3
import sys

source_path, backup_path = sys.argv[1], sys.argv[2]
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
target = sqlite3.connect(backup_path)
with target:
    source.backup(target)
target.close()
source.close()
PY

gzip -f "${tmp_db}"
mv "${tmp_db}.gz" "${daily_dir}/hypertrade-${date_stamp}.sqlite.gz"

if [ "$(date -u +%u)" = "7" ]; then
    cp "${daily_dir}/hypertrade-${date_stamp}.sqlite.gz" \
      "${weekly_dir}/hypertrade-${week_stamp}.sqlite.gz"
fi

find "${daily_dir}" -maxdepth 1 -type f -name 'hypertrade-*.sqlite.gz' | sort | head -n -7 | xargs -r rm -f
find "${weekly_dir}" -maxdepth 1 -type f -name 'hypertrade-*.sqlite.gz' | sort | head -n -4 | xargs -r rm -f
