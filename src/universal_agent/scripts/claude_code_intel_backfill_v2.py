"""CLI for the parallel-vault backfill (PR 12).

Replays every historical packet through the v2 pipeline (PRs 1-11, 15, 16)
and writes results into a parallel vault. After a manual inspection pass,
operator can run with --swap to atomic-rename the new vault into the
canonical position (and the old vault into the v1-archive position).

Sequenced operator workflow:

    # 1. Dry run — count packets, no writes.
    PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --dry-run

    # 2. Replay into the parallel vault. NO swap. Inspect afterwards.
    PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2

    # 3. Compute the diff vs canonical.
    PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --diff-only

    # 4. Inspect /artifacts/knowledge-vaults/claude-code-intelligence-v2/ manually.

    # 5. When satisfied, swap.
    PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --swap-only

    # 6. If the swap reveals a bad vault, revert.
    PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 --revert-swap

Exit codes:
    0 — operation succeeded as requested
    1 — at least one packet replay failed (other packets still attempted)
    2 — programmer error (missing packet root, etc.)

See docs/proactive_signals/claudedevs_intel_v2_design.md §12.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from universal_agent.durable.db import get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.backfill_v2 import (
    BackfillStats,
    canonical_vault_root,
    compute_vault_diff,
    enumerate_packets,
    packets_root_for,
    parallel_vault_root,
    revert_swap,
    run_backfill,
    swap_vaults,
)

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default="",
        help="Deployment profile for Infisical secret loading.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate packets and exit. No writes, no DB connections.",
    )
    parser.add_argument(
        "--diff-only",
        action="store_true",
        help="Print the canonical-vs-parallel vault diff and exit.",
    )
    parser.add_argument(
        "--swap-only",
        action="store_true",
        help="Skip replay; perform only the atomic swap of canonical and parallel vaults.",
    )
    parser.add_argument(
        "--revert-swap",
        action="store_true",
        help="Reverse a prior swap: archive becomes canonical, current canonical is parked.",
    )
    parser.add_argument(
        "--overwrite-archive",
        action="store_true",
        help="When swapping, overwrite an existing v1-archive instead of refusing.",
    )
    parser.add_argument(
        "--queue-task-hub",
        action="store_true",
        help="Re-queue Task Hub items during replay. OFF by default to avoid duplicating work.",
    )
    parser.add_argument(
        "--no-vault-write",
        action="store_true",
        help="Run replay without writing to the parallel vault. Useful for measuring just the timing.",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Halt on the first packet replay failure instead of continuing.",
    )
    return parser.parse_args()


def _emit(payload: dict[str, Any], *, code: int = 0) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))
    return code


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile or None)

    src_packets = packets_root_for()
    canonical = canonical_vault_root()
    parallel = parallel_vault_root()

    if args.dry_run:
        packets = enumerate_packets(src_packets)
        return _emit(
            {
                "mode": "dry_run",
                "packets_root": str(src_packets),
                "packet_count": len(packets),
                "first_packet": str(packets[0]) if packets else "",
                "last_packet": str(packets[-1]) if packets else "",
                "canonical_vault": str(canonical),
                "parallel_vault_target": str(parallel),
            }
        )

    if args.diff_only:
        return _emit(
            {
                "mode": "diff_only",
                "diff": compute_vault_diff(canonical, parallel),
            }
        )

    if args.revert_swap:
        result = revert_swap(canonical=canonical)
        code = 0 if result.swapped else 2
        return _emit({"mode": "revert_swap", "result": result.to_dict()}, code=code)

    if args.swap_only:
        result = swap_vaults(
            canonical=canonical,
            parallel=parallel,
            overwrite_archive=args.overwrite_archive,
        )
        code = 0 if result.swapped else 2
        return _emit({"mode": "swap_only", "result": result.to_dict()}, code=code)

    # Default: run the full backfill replay.
    if not src_packets.exists():
        return _emit(
            {"mode": "backfill", "ok": False, "reason": f"packets_root_missing: {src_packets}"},
            code=2,
        )

    write_vault = not args.no_vault_write
    with sqlite3.connect(get_activity_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        stats: BackfillStats = run_backfill(
            packets_root=src_packets,
            parallel_vault=parallel,
            queue_task_hub=args.queue_task_hub,
            write_vault=write_vault,
            conn=conn,
            stop_on_error=args.stop_on_error,
        )

    diff = compute_vault_diff(canonical, parallel)
    payload = {
        "mode": "backfill",
        "stats": stats.to_dict(),
        "diff": diff,
    }
    code = 0 if stats.packets_failed == 0 else 1
    return _emit(payload, code=code)


if __name__ == "__main__":
    sys.exit(main())
