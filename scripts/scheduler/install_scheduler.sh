#!/usr/bin/env bash
# DEPRECATED: `cne schedule install` installs the packaged,
# cross-platform timer, which runs `cne run daily` and then `cne run maintain`.
# Run scripts/scheduler/uninstall_scheduler.sh before switching, or both update the lake.
# Preserve host groups, vantage, cadence and config overrides on reinstall.
# CNE_GROUPS / CNE_SOURCE_VANTAGE explicitly override those saved choices.
# --check reports drift; --dry-run DIR renders without changing launchd.
# --daily-only updates the daily agent without adding/changing other jobs.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
echo "install_scheduler: deprecated; use \`cne schedule install\` instead." >&2
if [[ "$(uname)" != "Darwin" ]]; then
  echo "install_scheduler: launchd is macOS-only; use daily_pipeline.sh/stale_pipeline.sh with cron." >&2
  exit 1
fi
if [[ ! -x "$REPO_ROOT/.venv/bin/cne" ]]; then
  echo "install_scheduler: create the project venv first." >&2
  exit 1
fi
exec "${CNE_PYTHON:-$REPO_ROOT/.venv/bin/python}" "$REPO_ROOT/scripts/scheduler/scheduler_config.py" "$@"
