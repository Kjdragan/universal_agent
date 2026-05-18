"""Architecture Canvas — weekly drift check.

Runs Mondays 06:30 America/Chicago by default. Re-runs the build script's
pointer verification and surfaces drift in two ways:

- **Missing pointers** (a `source:` path no longer exists): the script exits
  non-zero. The cron service records the tick as a failed run, which is
  visible on the `/dashboard/cron-jobs` surface — operator notices in the
  normal cron-health flow.

- **Stale pointers** (a path exists but hasn't been touched within the
  green/amber/red freshness thresholds): the script writes a structured
  drift report to ``artifacts/architecture-canvas-drift/<date>.md`` for
  later inspection. Exit code stays 0 so the cron tick is "successful" —
  staleness is signal, not failure.

If both counts are zero, the script is silent (no artifact, exit 0).

Wired in by: `_ensure_architecture_canvas_drift_cron_job` in
``gateway_server.py``. Disable via ``UA_ARCH_CANVAS_DRIFT_ENABLED=0``.
Reschedule via ``UA_ARCH_CANVAS_DRIFT_CRON``.

See: docs/02_Subsystems/Architecture_Canvas_View.md §6.
"""
from __future__ import annotations

import datetime as dt
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_architecture_view.py"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture-canvas-drift"


def _run_verify() -> tuple[int, str]:
    """Run the build script in --verify-only mode, capture output."""
    proc = subprocess.run(
        ["uv", "run", str(BUILD_SCRIPT), "--verify-only"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    return proc.returncode, combined


def _parse_counts(output: str) -> tuple[int, int]:
    """Extract stale/missing counts from the build script's stdout."""
    stale = missing = 0
    for line in output.splitlines():
        if "stale:" in line and "missing:" in line:
            # Line shape: "[build] verified N pointers — stale: X, missing: Y"
            try:
                stale_str = line.split("stale:")[1].split(",")[0].strip()
                missing_str = line.split("missing:")[1].strip().rstrip(".")
                stale = int(stale_str)
                missing = int(missing_str)
            except (ValueError, IndexError):
                pass
    return stale, missing


def _write_drift_report(stale: int, missing: int, output: str) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    report_path = ARTIFACTS_DIR / f"{date_str}.md"
    body = (
        f"# Architecture Canvas drift report — {date_str}\n\n"
        f"- Stale pointers (amber/red, >30 days untouched): **{stale}**\n"
        f"- Missing pointers (path no longer exists): **{missing}**\n\n"
        "## Build script output\n\n"
        "```\n"
        f"{output.strip()}\n"
        "```\n\n"
        "## Next step\n\n"
        "Open `docs/architecture-view/sources/*.yaml`, find pointers whose paths "
        "are missing or whose targets have been renamed, and update them. Rerun "
        "`just canvas` after fixes to regenerate the rendered HTML.\n"
    )
    report_path.write_text(body, encoding="utf-8")
    return report_path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not BUILD_SCRIPT.exists():
        logger.error("Build script not found at %s", BUILD_SCRIPT)
        return 2

    rc, output = _run_verify()
    stale, missing = _parse_counts(output)
    logger.info(
        "architecture canvas verify: rc=%d stale=%d missing=%d", rc, stale, missing
    )

    if missing > 0:
        # Surface as a failed cron tick (visible in /dashboard/cron-jobs).
        logger.error(
            "MISSING POINTERS in architecture canvas — see build output above."
        )
        return 1 if rc == 0 else rc

    if stale > 0:
        report = _write_drift_report(stale, missing, output)
        logger.info("Wrote drift report to %s", report)
    else:
        logger.info("No drift detected; staying silent.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
