"""Pre-run workspace hygiene for the ``paper_to_podcast_daily`` cron.

The cron reuses ONE fixed workspace (``AGENT_RUN_WORKSPACES/cron_paper_to_podcast``)
across every daily run, and nothing ever cleaned it — so each run's deliverables
piled up next to prior runs'. That reused-and-never-cleaned directory is the root
cause of a recurring class of false success/failure calls: every downstream
component (the post-run guard, the artifact notifier) had to re-derive "is this
file from THIS run?" via mtime-vs-run-start heuristics, and those heuristics
mis-fired in both directions — 2026-06-10 a stale manifest got emailed as
tonight's podcast; 2026-07-09 a run produced a real podcast but left the JSON
sidecars stale and was called a "zero usable papers" no-op.

Clearing the run's OUTPUT directory *before* the run starts makes "does
``podcast_audio.m4a`` exist?" a true binary check instead of a freshness puzzle,
eliminating the stale-artifact class at its source. The freshness heuristics in
the guard and notifier remain only as a cheap backstop in case this wipe ever
fails; they are no longer the primary line of defence.

Only ``work_products/paper_to_podcast/`` is cleared. The workspace ROOT is left
intact so the deploy-restart resume checkpoint (``.nlm_resume.json``, written at
the root) survives — a run interrupted mid-generation can still adopt its
in-flight NotebookLM notebook and re-download into the freshly-cleared dir.
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

logger = logging.getLogger(__name__)

_OUTPUT_SUBPATH = "work_products/paper_to_podcast"


def prepare_run_workspace(workspace_dir: str | Path) -> int:
    """Clear the paper_to_podcast output dir before a run.

    Returns the number of top-level entries removed (0 if the dir was absent or
    already empty). Best-effort and NEVER raises: a cleanup failure must not
    block the run — the guard + notifier freshness checks still backstop
    staleness. The directory is (re)created empty so the skill can write into it
    immediately. The workspace root (and its ``.nlm_resume.json`` checkpoint) is
    untouched.
    """
    output_dir = Path(workspace_dir).expanduser() / _OUTPUT_SUBPATH
    removed = 0
    try:
        if output_dir.is_dir():
            removed = sum(1 for _ in output_dir.iterdir())
            shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "paper_to_podcast prep: workspace cleanup skipped for %s: %s",
            output_dir,
            exc,
        )
    return removed


__all__ = ["prepare_run_workspace"]
