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

Implementation note: this module loads ``scripts/build_architecture_view.py``
via :func:`importlib.util.spec_from_file_location` and calls its
``load_exhibits()`` + ``verify_pointers()`` functions directly. Earlier
versions shelled out to ``uv run scripts/build_architecture_view.py
--verify-only`` via :mod:`subprocess`, but that tripped the Task Hub
Observability Protocol guard (``tests/unit/test_task_observability_coverage.py``)
which requires every subprocess-spawning file in ``src/universal_agent/``
to import a compliant helper OR be in the allowlist. Importing the
verification functions directly is the right answer here — there is no
worker to observe, and the in-process call is faster than spawning a
fresh ``uv run`` per Monday tick (no venv warm-up, no shell layer).

Wired in by: ``_ensure_architecture_canvas_drift_cron_job`` in
``gateway_server.py``. Disable via ``UA_ARCH_CANVAS_DRIFT_ENABLED=0``.
Reschedule via ``UA_ARCH_CANVAS_DRIFT_CRON``.

See: docs/02_Subsystems/Architecture_Canvas_View.md §6.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_architecture_view.py"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture-canvas-drift"


def _load_build_module() -> ModuleType:
    """Load `scripts/build_architecture_view.py` as a module without spawning
    a subprocess. The build script's ``if __name__ == "__main__":`` guard is
    not triggered because we set the module name to ``_canvas_build``.

    The module is registered in :data:`sys.modules` BEFORE
    ``spec.loader.exec_module`` runs. Without this step, the build script's
    use of ``@dataclass`` raises ``AttributeError: 'NoneType' object has no
    attribute '__dict__'`` on Python 3.13 because
    ``dataclasses._is_type`` calls ``sys.modules.get(cls.__module__).__dict__``
    while processing each dataclass field. See
    https://docs.python.org/3/library/importlib.html#importing-a-source-file-directly.
    """
    spec = importlib.util.spec_from_file_location("_canvas_build", BUILD_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build importlib spec for {BUILD_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_canvas_build"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_drift_report(stale: int, missing: int, statuses: dict[str, Any]) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    report_path = ARTIFACTS_DIR / f"{date_str}.md"

    stale_lines: list[str] = []
    missing_lines: list[str] = []
    for path, status in sorted(statuses.items()):
        if not status.exists:
            missing_lines.append(f"- `{path}` — path no longer exists")
        elif status.badge in ("red", "amber"):
            age = f"{status.days_ago}d" if status.days_ago is not None else "unknown age"
            stale_lines.append(f"- `{path}` — {status.badge}, last touched {age}")

    body_parts = [
        f"# Architecture Canvas drift report — {date_str}",
        "",
        f"- Stale pointers (amber/red, >60 days untouched): **{stale}**",
        f"- Missing pointers (path no longer exists): **{missing}**",
        "",
    ]
    if missing_lines:
        body_parts.extend(["## Missing pointers", ""] + missing_lines + [""])
    if stale_lines:
        body_parts.extend(["## Stale pointers", ""] + stale_lines + [""])
    body_parts.extend([
        "## Next step",
        "",
        "Open `docs/architecture-view/sources/*.yaml`, find pointers whose paths "
        "are missing or whose targets have been renamed, and update them. Rerun "
        "`just canvas` after fixes to regenerate the rendered HTML.",
        "",
    ])
    report_path.write_text("\n".join(body_parts), encoding="utf-8")
    return report_path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not BUILD_SCRIPT.exists():
        logger.error("Build script not found at %s", BUILD_SCRIPT)
        return 2

    try:
        build = _load_build_module()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load build module: %s", exc)
        return 2

    try:
        exhibits = build.load_exhibits()
        statuses, stale, missing = build.verify_pointers(exhibits)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pointer verification failed: %s", exc)
        return 2

    logger.info(
        "architecture canvas verify: total=%d stale=%d missing=%d",
        len(statuses), stale, missing,
    )

    if missing > 0:
        # Surface as a failed cron tick (visible in /dashboard/cron-jobs).
        for path, status in sorted(statuses.items()):
            if not status.exists:
                logger.error("  missing pointer: %s", path)
        return 1

    if stale > 0:
        report = _write_drift_report(stale, missing, statuses)
        logger.info("Wrote drift report to %s", report)
    else:
        logger.info("No drift detected; staying silent.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
