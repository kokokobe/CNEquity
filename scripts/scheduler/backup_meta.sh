#!/usr/bin/env bash
# B3 — Daily snapshot of the metadata that cannot be rebuilt from the curated
# lake. Deprecated with the rest of scripts/scheduler: the
# packaged timer (`cne schedule install`) runs `cne run maintain`, which makes
# the same backup. The work itself is `cnequity.storage.meta_backup`; this
# wrapper keeps the old calling convention for existing launchd jobs.
#
# Usage: scripts/scheduler/backup_meta.sh [DATA_ROOT] [BACKUP_DIR] [RETENTION_DAYS] [RETENTION_COUNT]
# Defaults resolve to the repo's ./data/cnequity lake.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n "${CNE_PYTHON:-}" ]]; then
  PY="$CNE_PYTHON"
elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="python3"
fi
DATA_ROOT="${1:-${CNE_DATA_ROOT:-$REPO_ROOT/data/cnequity}}"
ARGS=("$DATA_ROOT" "${2:-${CNE_BACKUP_DIR:-$DATA_ROOT/backups}}"
  "${3:-${CNE_BACKUP_RETENTION_DAYS:-14}}" "${4:-${CNE_BACKUP_RETENTION_COUNT:-30}}")
exec "$PY" -m cnequity.storage.meta_backup "${ARGS[@]}"
