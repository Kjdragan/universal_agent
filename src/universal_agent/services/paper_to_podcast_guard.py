"""Post-run fail-loud guard for the ``paper_to_podcast_daily`` cron.

This module implements the MECHANICAL fail-loud guard that runs after the LLM
coroutine returns and flips a would-be ``clean_exit_zero`` run to NON-clean
when the run produced zero usable papers.

Why this exists
---------------
The 2026-06-22 silent no-op: the paper_to_podcast_daily cron run exited
``clean_exit_zero / status=success`` in 5 minutes but emitted no email and
produced no podcast. The agent\'s LLM narrative concluded "none of my 5 targets
are in the local cache" (a self-diagnosis based on a mis-timed cache check) and
gracefully ended the turn — rc=0 — so the cron\'s exit classifier painted it
as a clean success. The watchdog only caught it ~32h later via the email-gap
invariant.

The fix layers two defences:

1. Cache-path alignment (``arxiv_runtime.canonical_arxiv_storage_path`` /
   ``is_paper_cached``) so a successfully-downloaded paper IS found by the
   pipeline\'s cache check. This prevents the bug from recurring for its
   primary root cause.

2. THIS module — a deterministic post-run guard that does NOT trust the LLM\'s
   self-report. It inspects the run\'s actual work products and, for a
   paper_to_podcast run that ended rc=0, requires evidence that >=1 paper was
   actually usable. If not, it flips the run to failed so the
   ``cron_consecutive_failures`` invariant, the watchdog, and the operator
   all see a real failure instead of a silent no-op.

The guard is pure (no DB / no sockets) so it can run inline in the cron\'s
Phase F.1 close path without risk of masking the original error.

Success evidence accepted (ANY one of):
  * ``work_products/paper_to_podcast/manifest.json`` exists AND lists >=1
    paper in its ``papers`` field.
  * ``work_products/paper_to_podcast/papers_metadata.json`` exists AND lists
    >=1 paper.
  * ``work_products/paper_to_podcast/FAILURE.txt`` exists — the skill\'s
    Phase A step 5 instructs the agent to write this when zero papers
    downloaded. Its presence is treated as an EXPLICIT failure signal
    (the guard flips to failed; its absence with no other evidence is also
    a failure).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Relative to the cron workspace root. The paper_to_podcast skill writes all
# deliverables under work_products/paper_to_podcast/ (see SKILL.md
# "Output Structure").
_WORK_PRODUCTS_SUBPATH = "work_products/paper_to_podcast"
_MANIFEST_FILENAME = "manifest.json"
_PAPERS_METADATA_FILENAME = "papers_metadata.json"
_FAILURE_SENTINEL_FILENAME = "FAILURE.txt"


@dataclass(frozen=True)
class PaperToPodcastRunResult:
    """Outcome of the post-run guard for a paper_to_podcast run.

    Attributes:
        is_failure: ``True`` iff the run should be classified as a failure
            (zero usable papers / missing success evidence). When ``True``,
            the caller flips rc_equiv to 1 and records ``reason`` as the
            run\'s error text.
        reason: Human-readable explanation. On failure, names the missing
            evidence; on success, names the evidence that satisfied the guard.
        usable_paper_count: Best-effort count of papers recorded in the
            manifest/metadata. ``0`` on failure-by-zero-papers.
    """

    is_failure: bool
    reason: str
    usable_paper_count: int


def _count_papers_in_json(path: Path) -> Optional[int]:
    """Return the number of papers recorded in a manifest/metadata JSON file.

    Accepts either ``{"papers": [...]}`` (manifest) or ``[...]`` (a bare
    list of paper metadata). Returns ``None`` if the file is absent or
    unparseable so the caller can fall through to other evidence.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("paper_to_podcast_guard: could not parse %s: %s", path, exc)
        return None
    if isinstance(data, dict):
        papers = data.get("papers")
        if isinstance(papers, list):
            return len(papers)
        return None
    if isinstance(data, list):
        return len(data)
    return None


def _is_fresh(path: Path, run_started_at: Optional[float]) -> bool:
    """True iff ``path`` was written by THIS run — mtime at/after the run start.

    When ``run_started_at`` is None (caller didn't pass it), freshness is NOT
    enforced (legacy behavior). 2s slack absorbs fs/rounding jitter.
    """
    if run_started_at is None:
        return True
    try:
        return path.stat().st_mtime >= float(run_started_at) - 2
    except OSError:
        return False


def evaluate_paper_to_podcast_run(
    workspace_dir: str | Path,
    run_started_at: Optional[float] = None,
) -> PaperToPodcastRunResult:
    """Inspect a paper_to_podcast run\'s work products for success evidence.

    Pure: reads the filesystem under ``workspace_dir`` only. Never raises —
    any unreadable / missing artifact is treated as missing evidence.

    Args:
        workspace_dir: The cron run\'s workspace root (the directory that
            contains ``work_products/``).

    Returns:
        A ``PaperToPodcastRunResult``. ``is_failure=True`` when zero usable
        papers are evidenced AND no explicit FAILURE sentinel exists.
    """
    work_products = Path(workspace_dir).expanduser().resolve() / _WORK_PRODUCTS_SUBPATH
    manifest_path = work_products / _MANIFEST_FILENAME
    papers_meta_path = work_products / _PAPERS_METADATA_FILENAME
    failure_sentinel_path = work_products / _FAILURE_SENTINEL_FILENAME

    # Explicit failure signal from the skill (Phase A step 5) — the agent
    # wrote FAILURE.txt declaring zero papers downloaded. Honour it ONLY if it
    # is from THIS run: a STALE FAILURE.txt left by an earlier failed run must
    # NOT flip a later successful run to failed. (2026-07-02: a morning
    # auth-fail sentinel tripped the evening success run → a false
    # "[ERROR] Autonomous Task Failed" email even though a real podcast landed.)
    if failure_sentinel_path.is_file() and _is_fresh(failure_sentinel_path, run_started_at):
        try:
            detail = failure_sentinel_path.read_text(encoding="utf-8").strip()[:300]
        except OSError:
            detail = ""
        reason = (
            "paper_to_podcast FAILURE.txt sentinel present"
            + (f": {detail}" if detail else "")
        )
        return PaperToPodcastRunResult(is_failure=True, reason=reason, usable_paper_count=0)

    # Success evidence: manifest.json with >=1 paper — but only if THIS run
    # wrote it (a stale manifest from a prior success must not vouch for a run
    # that produced nothing this time).
    manifest_count = (
        _count_papers_in_json(manifest_path)
        if _is_fresh(manifest_path, run_started_at)
        else None
    )
    if manifest_count is not None and manifest_count >= 1:
        return PaperToPodcastRunResult(
            is_failure=False,
            reason=f"manifest.json lists {manifest_count} paper(s)",
            usable_paper_count=manifest_count,
        )

    # Fallback success evidence: papers_metadata.json with >=1 paper (fresh only).
    meta_count = (
        _count_papers_in_json(papers_meta_path)
        if _is_fresh(papers_meta_path, run_started_at)
        else None
    )
    if meta_count is not None and meta_count >= 1:
        return PaperToPodcastRunResult(
            is_failure=False,
            reason=f"papers_metadata.json lists {meta_count} paper(s)",
            usable_paper_count=meta_count,
        )

    # No success evidence. Determine the best description.
    if manifest_count == 0 and meta_count == 0:
        reason = (
            "paper_to_podcast produced ZERO usable papers: manifest.json and "
            "papers_metadata.json both empty/absent under "
            f"{_WORK_PRODUCTS_SUBPATH}/ (likely an arxiv download_paper / "
            "cache-path failure — the run would have silently no-op'd pre-guard)"
        )
    elif manifest_count == 0:
        reason = (
            "paper_to_podcast manifest.json lists 0 papers (papers_metadata.json "
            "absent/unparseable) — zero usable papers"
        )
    elif meta_count == 0:
        reason = (
            "paper_to_podcast papers_metadata.json lists 0 papers (manifest.json "
            "absent/unparseable) — zero usable papers"
    )
    else:
        # Both None (unparseable or absent).
        reason = (
            "paper_to_podcast produced no manifest.json and no "
            "papers_metadata.json under "
            f"{_WORK_PRODUCTS_SUBPATH}/ — zero usable papers (run no-op'd)"
        )
    return PaperToPodcastRunResult(is_failure=True, reason=reason, usable_paper_count=0)


__all__ = ["PaperToPodcastRunResult", "evaluate_paper_to_podcast_run"]
