"""Parallel-vault backfill of historical packets through the v2 pipeline (PR 12).

Per the v2 design (§12), the existing v1 vault content was built by the
old pipeline (3K context cap, no Memex updates, no research grounding).
It can't be repaired in place. Plan: rebuild via parallel-vault staging.

Pipeline:
  1. Provision a parallel vault at
     `<artifacts>/knowledge-vaults/<KB_SLUG>-v2/` (or wherever the
     UA_CSI_VAULT_PATH_OVERRIDE env points).
  2. Walk every packet under `artifacts/proactive/<LANE_SLUG>/packets/`
     and replay it through replay_packet with the override active.
  3. Compute a diff summary against the canonical vault.
  4. Atomic swap: rename canonical → archive, parallel → canonical.

This module performs no LLM calls itself — it orchestrates the existing
replay machinery. URL fetches inside replay reuse cached content under
`packets/.../url_enrichment/` when available; cold cache falls through
to fresh fetches via csi_url_judge.

See docs/proactive_signals/claudedevs_intel_v2_design.md §12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.services.claude_code_intel import KB_SLUG, LANE_SLUG

logger = logging.getLogger(__name__)


VAULT_PATH_OVERRIDE_ENV = "UA_CSI_VAULT_PATH_OVERRIDE"
DEFAULT_PARALLEL_SUFFIX = "-v2"
ARCHIVE_SUFFIX = "-v1-archive"


# ── Path helpers ────────────────────────────────────────────────────────────


def packets_root_for(artifacts_root: Path | None = None) -> Path:
    return (artifacts_root or resolve_artifacts_dir()) / "proactive" / LANE_SLUG / "packets"


def canonical_vault_root(artifacts_root: Path | None = None) -> Path:
    return (artifacts_root or resolve_artifacts_dir()) / "knowledge-vaults" / KB_SLUG


def parallel_vault_root(artifacts_root: Path | None = None, *, suffix: str = DEFAULT_PARALLEL_SUFFIX) -> Path:
    return (artifacts_root or resolve_artifacts_dir()) / "knowledge-vaults" / f"{KB_SLUG}{suffix}"


def archive_vault_root(artifacts_root: Path | None = None) -> Path:
    return (artifacts_root or resolve_artifacts_dir()) / "knowledge-vaults" / f"{KB_SLUG}{ARCHIVE_SUFFIX}"


# ── Packet enumeration ──────────────────────────────────────────────────────


def enumerate_packets(packets_root: Path) -> list[Path]:
    """Return every packet directory under `packets_root`, sorted oldest first.

    Layout: `<packets_root>/<YYYY-MM-DD>/<HHMMSS>__<handle>/`. Sorted by
    (date_dir, packet_dir) so replay processes packets chronologically —
    important because Memex EXTEND on a known entity depends on the page
    already existing from a prior CREATE.
    """
    if not packets_root.exists():
        return []
    out: list[Path] = []
    for date_dir in sorted(packets_root.iterdir()):
        if not date_dir.is_dir():
            continue
        for packet_dir in sorted(date_dir.iterdir()):
            if not packet_dir.is_dir():
                continue
            if not (packet_dir / "manifest.json").exists():
                continue
            out.append(packet_dir)
    return out


# ── Backfill orchestration ──────────────────────────────────────────────────


@dataclass
class PacketReplayRecord:
    packet_dir: str
    ok: bool
    error: str = ""
    new_post_count: int = 0
    action_count: int = 0
    memex_action_count: int = 0
    grounded_source_count: int = 0


@dataclass
class BackfillStats:
    started_at: str
    finished_at: str = ""
    packets_total: int = 0
    packets_replayed_ok: int = 0
    packets_failed: int = 0
    parallel_vault: str = ""
    records: list[PacketReplayRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "packets_total": self.packets_total,
            "packets_replayed_ok": self.packets_replayed_ok,
            "packets_failed": self.packets_failed,
            "parallel_vault": self.parallel_vault,
            "records": [
                {
                    "packet_dir": r.packet_dir,
                    "ok": r.ok,
                    "error": r.error,
                    "new_post_count": r.new_post_count,
                    "action_count": r.action_count,
                    "memex_action_count": r.memex_action_count,
                    "grounded_source_count": r.grounded_source_count,
                }
                for r in self.records
            ],
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_backfill(
    *,
    packets_root: Path | None = None,
    parallel_vault: Path | None = None,
    artifacts_root: Path | None = None,
    queue_task_hub: bool = False,
    expand_sources: bool = True,
    write_vault: bool = True,
    conn: sqlite3.Connection | None = None,
    stop_on_error: bool = False,
) -> BackfillStats:
    """Walk every packet and replay it into the parallel vault.

    Sets the UA_CSI_VAULT_PATH_OVERRIDE env var for the duration of the
    run so replay machinery writes into the parallel vault instead of
    the canonical one. Restores the env on exit.

    queue_task_hub defaults to False because backfill replays should NOT
    re-queue Task Hub items that may have already been processed in v1.
    """
    # Lazy import — replay_packet has heavy transitive deps that we don't
    # need until we're actually backfilling.
    from universal_agent.services.claude_code_intel_replay import (
        ClaudeCodeIntelReplayConfig,
        replay_packet,
    )

    src_packets = packets_root or packets_root_for(artifacts_root)
    target_vault = parallel_vault or parallel_vault_root(artifacts_root)
    target_vault.mkdir(parents=True, exist_ok=True)

    stats = BackfillStats(started_at=_now_iso(), parallel_vault=str(target_vault))
    packets = enumerate_packets(src_packets)
    stats.packets_total = len(packets)

    prior_override = os.environ.get(VAULT_PATH_OVERRIDE_ENV)
    os.environ[VAULT_PATH_OVERRIDE_ENV] = str(target_vault)
    try:
        for packet_dir in packets:
            record = PacketReplayRecord(packet_dir=str(packet_dir), ok=False)
            try:
                config = ClaudeCodeIntelReplayConfig(
                    packet_dir=packet_dir,
                    queue_task_hub=queue_task_hub,
                    write_vault=write_vault,
                    expand_sources=expand_sources,
                    artifacts_root=artifacts_root,
                )
                result = replay_packet(config=config, conn=conn)
                record.ok = bool(result.get("ok"))
                record.new_post_count = int(result.get("new_post_count") or 0)
                record.action_count = int(result.get("action_count") or 0)
                record.memex_action_count = len(
                    (result.get("memex_actions") or [])
                    if isinstance(result.get("memex_actions"), list)
                    else []
                )
                record.grounded_source_count = int(result.get("grounded_source_count") or 0)
                if record.ok:
                    stats.packets_replayed_ok += 1
                else:
                    stats.packets_failed += 1
            except Exception as exc:
                record.ok = False
                record.error = f"{type(exc).__name__}: {exc}"
                stats.packets_failed += 1
                logger.exception("backfill replay failed for %s", packet_dir)
                if stop_on_error:
                    stats.records.append(record)
                    break
            stats.records.append(record)
    finally:
        if prior_override is None:
            os.environ.pop(VAULT_PATH_OVERRIDE_ENV, None)
        else:
            os.environ[VAULT_PATH_OVERRIDE_ENV] = prior_override

    stats.finished_at = _now_iso()
    return stats


# ── Vault diff ──────────────────────────────────────────────────────────────


def _count_files(root: Path, pattern: str = "*.md") -> int:
    if not root.exists():
        return 0
    return sum(1 for _ in root.rglob(pattern))


def compute_vault_diff(canonical: Path, parallel: Path) -> dict[str, Any]:
    """Coarse summary of what the backfill produced vs the canonical vault."""
    return {
        "canonical_path": str(canonical),
        "parallel_path": str(parallel),
        "canonical_exists": canonical.exists(),
        "parallel_exists": parallel.exists(),
        "canonical": {
            "total_md": _count_files(canonical),
            "entities": _count_files(canonical / "entities"),
            "concepts": _count_files(canonical / "concepts"),
            "sources": _count_files(canonical / "sources"),
            "raw": _count_files(canonical / "raw"),
            "history": _count_files(canonical / "_history"),
        },
        "parallel": {
            "total_md": _count_files(parallel),
            "entities": _count_files(parallel / "entities"),
            "concepts": _count_files(parallel / "concepts"),
            "sources": _count_files(parallel / "sources"),
            "raw": _count_files(parallel / "raw"),
            "history": _count_files(parallel / "_history"),
        },
    }


# ── Atomic swap ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SwapResult:
    canonical_path: str
    archive_path: str
    parallel_path: str
    swapped: bool
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "archive_path": self.archive_path,
            "parallel_path": self.parallel_path,
            "swapped": self.swapped,
            "skipped_reason": self.skipped_reason,
        }


def swap_vaults(
    *,
    canonical: Path,
    parallel: Path,
    archive: Path | None = None,
    overwrite_archive: bool = False,
) -> SwapResult:
    """Atomic swap: canonical → archive, parallel → canonical.

    Refuses to clobber an existing archive unless overwrite_archive=True.
    Refuses to swap if parallel doesn't exist (sanity check).
    Idempotent in the sense that calling it after a successful swap with
    the same inputs returns skipped_reason="parallel_missing" because
    the parallel was renamed in the previous call.
    """
    archive_path = archive or canonical.with_name(canonical.name + ARCHIVE_SUFFIX)

    if not parallel.exists():
        return SwapResult(
            canonical_path=str(canonical),
            archive_path=str(archive_path),
            parallel_path=str(parallel),
            swapped=False,
            skipped_reason="parallel_missing",
        )
    if not parallel.is_dir():
        return SwapResult(
            canonical_path=str(canonical),
            archive_path=str(archive_path),
            parallel_path=str(parallel),
            swapped=False,
            skipped_reason="parallel_not_directory",
        )
    if archive_path.exists() and not overwrite_archive:
        return SwapResult(
            canonical_path=str(canonical),
            archive_path=str(archive_path),
            parallel_path=str(parallel),
            swapped=False,
            skipped_reason=f"archive_exists: {archive_path}",
        )
    if archive_path.exists() and overwrite_archive:
        shutil.rmtree(archive_path)

    if canonical.exists():
        canonical.rename(archive_path)
    parallel.rename(canonical)
    return SwapResult(
        canonical_path=str(canonical),
        archive_path=str(archive_path),
        parallel_path=str(parallel),
        swapped=True,
    )


def revert_swap(
    *,
    canonical: Path,
    archive: Path | None = None,
    parallel_restore_path: Path | None = None,
) -> SwapResult:
    """Reverse a swap: canonical → parallel_restore_path, archive → canonical.

    Used when post-swap inspection reveals the new vault is bad. The
    archive directory is restored to the canonical name; the previously-
    canonical (now-broken) vault is parked at parallel_restore_path so
    it can be inspected.
    """
    archive_path = archive or canonical.with_name(canonical.name + ARCHIVE_SUFFIX)
    target_for_current = parallel_restore_path or canonical.with_name(canonical.name + "-rolledback")

    if not archive_path.exists():
        return SwapResult(
            canonical_path=str(canonical),
            archive_path=str(archive_path),
            parallel_path=str(target_for_current),
            swapped=False,
            skipped_reason="archive_missing",
        )
    if canonical.exists():
        if target_for_current.exists():
            shutil.rmtree(target_for_current)
        canonical.rename(target_for_current)
    archive_path.rename(canonical)
    return SwapResult(
        canonical_path=str(canonical),
        archive_path=str(archive_path),
        parallel_path=str(target_for_current),
        swapped=True,
    )
