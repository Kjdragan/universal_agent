"""Post-run fail-loud guard for the ``paper_to_podcast_daily`` cron.

This module implements the MECHANICAL fail-loud guard that runs after the LLM
coroutine returns and flips a would-be ``clean_exit_zero`` run to NON-clean
when the run genuinely produced nothing.

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
   paper_to_podcast run that ended rc=0, requires evidence that the run really
   produced its deliverable. If not, it flips the run to failed so the
   ``cron_consecutive_failures`` invariant, the watchdog, and the operator all
   see a real failure instead of a silent no-op.

Ground truth is the podcast, not the bookkeeping
------------------------------------------------
The SKILL\'s headline deliverable is the audio overview ``.m4a`` ("REQUIRED —
the headline deliverable"). The ``manifest.json`` / ``papers_metadata.json``
files are bookkeeping sidecars the LLM is *supposed* to write, but in practice
sometimes forgets to after the podcast is already made (observed 2026-07-09: a
run downloaded a real 38 MB ``podcast_audio.m4a`` + quiz + flashcards, published
a report to the scratchpad, and self-reported success — but never re-wrote the
two JSON sidecars, so an earlier version of this guard misread a real success as
"zero usable papers / no-op" and suppressed the podcast email).

So the guard accepts the **actual deliverable** as first-class success
evidence: a fresh, real ``podcast_audio.m4a`` proves the run succeeded (you
cannot produce a NotebookLM audio overview from zero sources). Freshness + a
minimum size mean a genuine no-op — which leaves no fresh podcast behind —
still fails, so the 2026-06-22 silent-no-op class cannot recur.

The guard is pure (no DB / no sockets) so it can run inline in the cron\'s
Phase F.1 close path without risk of masking the original error.

Success evidence accepted (ANY one of):
  * A fresh, real ``work_products/paper_to_podcast/podcast_audio.m4a``
    (mtime at/after the run start, size >= ``_MIN_PODCAST_AUDIO_BYTES``) —
    the headline deliverable. This is ground truth and is checked first.
  * ``work_products/paper_to_podcast/manifest.json`` exists AND lists >=1
    paper in its ``papers`` field.
  * ``work_products/paper_to_podcast/papers_metadata.json`` exists AND lists
    >=1 paper.

Explicit failure signal (honoured over the sidecars, but only if fresh):
  * ``work_products/paper_to_podcast/FAILURE.txt`` — the skill instructs the
    agent to write this when zero papers downloaded. A fresh one flips the run
    to failed.
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
_PODCAST_AUDIO_FILENAME = "podcast_audio.m4a"

# A real NotebookLM audio overview is multiple MB; the SKILL (Phase C step 1)
# verifies the download is "> 100 KB (a real .m4a)". We use the same floor so a
# truncated / failed-download stub cannot masquerade as a produced podcast.
_MIN_PODCAST_AUDIO_BYTES = 100 * 1024


@dataclass(frozen=True)
class PaperToPodcastRunResult:
    """Outcome of the post-run guard for a paper_to_podcast run.

    Attributes:
        is_failure: ``True`` iff the run should be classified as a failure
            (no deliverable / missing success evidence). When ``True``,
            the caller flips rc_equiv to 1 and records ``reason`` as the
            run\'s error text.
        reason: Human-readable explanation. On failure, names the missing
            evidence; on success, names the evidence that satisfied the guard.
        usable_paper_count: Best-effort count of papers recorded in the
            manifest/metadata. ``0`` on failure-by-zero-papers, and may also
            be ``0`` on a podcast-evidenced success where the agent skipped the
            paper sidecars (the podcast itself is proof papers existed).
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


def _fresh_podcast_audio_bytes(
    path: Path, run_started_at: Optional[float]
) -> Optional[int]:
    """Return the podcast ``.m4a`` size in bytes iff it is real success evidence.

    Real == the file exists, was written by THIS run (fresh), and is at least
    ``_MIN_PODCAST_AUDIO_BYTES`` (so a 0-byte / truncated stub from a failed
    download is not mistaken for a produced podcast). Returns ``None`` otherwise
    so the caller falls through to the sidecar-based evidence.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    if not _is_fresh(path, run_started_at):
        return None
    if st.st_size < _MIN_PODCAST_AUDIO_BYTES:
        return None
    return st.st_size


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
        run_started_at: Epoch seconds of this run\'s start. When given, only
            artifacts written at/after it count — a reused cron workspace
            accumulates prior runs\' deliverables, which must not vouch for a
            run that produced nothing this time.

    Returns:
        A ``PaperToPodcastRunResult``. ``is_failure=True`` when no fresh
        deliverable is evidenced AND no explicit FAILURE sentinel exists.
    """
    work_products = Path(workspace_dir).expanduser().resolve() / _WORK_PRODUCTS_SUBPATH
    manifest_path = work_products / _MANIFEST_FILENAME
    papers_meta_path = work_products / _PAPERS_METADATA_FILENAME
    failure_sentinel_path = work_products / _FAILURE_SENTINEL_FILENAME
    podcast_audio_path = work_products / _PODCAST_AUDIO_FILENAME

    # Explicit failure signal from the skill — the agent wrote FAILURE.txt
    # declaring zero papers downloaded. Honour it ONLY if it is from THIS run:
    # a STALE FAILURE.txt left by an earlier failed run must NOT flip a later
    # successful run to failed. (2026-07-02: a morning auth-fail sentinel
    # tripped the evening success run → a false "[ERROR] Autonomous Task
    # Failed" email even though a real podcast landed.)
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

    # Fresh paper counts (computed once; reused for the count field below).
    manifest_count = (
        _count_papers_in_json(manifest_path)
        if _is_fresh(manifest_path, run_started_at)
        else None
    )
    meta_count = (
        _count_papers_in_json(papers_meta_path)
        if _is_fresh(papers_meta_path, run_started_at)
        else None
    )

    # GROUND TRUTH — the headline deliverable. A fresh, real podcast_audio.m4a
    # proves the run succeeded regardless of whether the agent also wrote the
    # manifest/papers_metadata bookkeeping (which it sometimes forgets after the
    # podcast is already made — 2026-07-09). Checked before the sidecars because
    # it is the actual output the pipeline exists to produce.
    audio_bytes = _fresh_podcast_audio_bytes(podcast_audio_path, run_started_at)
    if audio_bytes is not None:
        # Best-effort paper count for observability; 0 if the sidecars were
        # skipped (the podcast itself evidences that papers existed).
        count = 0
        if manifest_count is not None and manifest_count >= 1:
            count = manifest_count
        elif meta_count is not None and meta_count >= 1:
            count = meta_count
        return PaperToPodcastRunResult(
            is_failure=False,
            reason=(
                f"podcast_audio.m4a present ({audio_bytes} bytes) — headline "
                "deliverable produced"
            ),
            usable_paper_count=count,
        )

    # Success evidence: manifest.json with >=1 paper — but only if THIS run
    # wrote it (a stale manifest from a prior success must not vouch for a run
    # that produced nothing this time).
    if manifest_count is not None and manifest_count >= 1:
        return PaperToPodcastRunResult(
            is_failure=False,
            reason=f"manifest.json lists {manifest_count} paper(s)",
            usable_paper_count=manifest_count,
        )

    # Fallback success evidence: papers_metadata.json with >=1 paper (fresh only).
    if meta_count is not None and meta_count >= 1:
        return PaperToPodcastRunResult(
            is_failure=False,
            reason=f"papers_metadata.json lists {meta_count} paper(s)",
            usable_paper_count=meta_count,
        )

    # No success evidence (and no fresh podcast). Determine the best description.
    if manifest_count == 0 and meta_count == 0:
        reason = (
            "paper_to_podcast produced ZERO usable papers and no podcast_audio.m4a: "
            "manifest.json and papers_metadata.json both empty/absent under "
            f"{_WORK_PRODUCTS_SUBPATH}/ (likely an arxiv download_paper / "
            "cache-path failure — the run would have silently no-op'd pre-guard)"
        )
    elif manifest_count == 0:
        reason = (
            "paper_to_podcast manifest.json lists 0 papers (papers_metadata.json "
            "absent/unparseable, no podcast_audio.m4a) — zero usable papers"
        )
    elif meta_count == 0:
        reason = (
            "paper_to_podcast papers_metadata.json lists 0 papers (manifest.json "
            "absent/unparseable, no podcast_audio.m4a) — zero usable papers"
        )
    else:
        # Both None (unparseable or absent) and no fresh podcast.
        reason = (
            "paper_to_podcast produced no podcast_audio.m4a, no manifest.json "
            "and no papers_metadata.json under "
            f"{_WORK_PRODUCTS_SUBPATH}/ — zero usable papers (run no-op'd)"
        )
    return PaperToPodcastRunResult(is_failure=True, reason=reason, usable_paper_count=0)


__all__ = ["PaperToPodcastRunResult", "evaluate_paper_to_podcast_run"]
