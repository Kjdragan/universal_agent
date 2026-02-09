#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUN_DIR="${ROOT_DIR}/OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews"

STATUS_FILE="${1:-}"
if [[ -z "$STATUS_FILE" ]]; then
  STATUS_FILE="$(ls -1t "${RUN_DIR}"/scheduling_v2_soak_24h_*.status.json 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "$STATUS_FILE" || ! -f "$STATUS_FILE" ]]; then
  echo "No soak status file found."
  exit 1
fi

uv run python - <<PY
import json
from pathlib import Path
p = Path(r"""$STATUS_FILE""")
d = json.loads(p.read_text(encoding="utf-8"))
s = d.get("summary", {})
print(json.dumps({
    "status_file": str(p),
    "updated_at": d.get("updated_at"),
    "running": d.get("running"),
    "elapsed_seconds": d.get("elapsed_seconds"),
    "remaining_seconds": d.get("remaining_seconds"),
    "cycles": s.get("cycles"),
    "total_checks": s.get("total_checks"),
    "total_fail": s.get("total_fail"),
    "all_checks_ok_so_far": s.get("all_checks_ok"),
    "report_path": d.get("report_path"),
}, indent=2))
PY
