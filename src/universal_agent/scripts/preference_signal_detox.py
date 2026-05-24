"""One-shot detox of poisoned implicit-outcome preference signals.

Background: prior to the gate-scoping fix in `proactive_preferences.py`,
`should_block_proactive_task` counted both explicit feedback and implicit
terminal-state outcomes (park/block/complete) toward the same per-key
weight. A 224-event one-day park burst on 2026-04-18→19 against the
convergence/insight pipeline saturated `source:convergence_detection`,
`topic:convergence`, `topic:atlas`, `topic:research` at -1.0 and silently
suppressed every brief produced thereafter (1119 insight + 571 convergence
artifacts stuck as `candidate`, zero downstream task_hub_items).

With the gate now scoped to `signal_type='explicit_feedback'`, those
implicit rows no longer block. They are still noise in the snapshot
(skewing weights used for ranking and the weekly digest), so this script
deletes them and rebuilds the snapshot.

Idempotent. Re-running finds zero rows and exits clean. Defaults to
dry-run; pass `--commit` to actually delete.

Usage:
    uv run python -m universal_agent.scripts.preference_signal_detox \\
        [--db /path/to/activity_state.db] [--commit] [--signal-type implicit_outcome]
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.services.proactive_preferences import (
    ensure_schema,
    rebuild_preference_snapshot,
)

logger = logging.getLogger(__name__)

DEFAULT_SIGNAL_TYPE = "implicit_outcome"


def detox(
    conn: sqlite3.Connection,
    *,
    signal_type: str = DEFAULT_SIGNAL_TYPE,
    commit: bool = False,
) -> dict[str, int]:
    """Delete rows of the given signal_type and rebuild the snapshot.

    Returns a report dict with `total_before`, `target_rows`, `deleted`,
    `total_after`, and `model_signals_after`.
    """
    ensure_schema(conn)

    total_before = conn.execute(
        "SELECT COUNT(*) FROM proactive_preference_signals"
    ).fetchone()[0]
    target_rows = conn.execute(
        "SELECT COUNT(*) FROM proactive_preference_signals WHERE signal_type = ?",
        (signal_type,),
    ).fetchone()[0]

    if not commit:
        return {
            "total_before": int(total_before),
            "target_rows": int(target_rows),
            "deleted": 0,
            "total_after": int(total_before),
            "model_signals_after": -1,
            "dry_run": 1,
        }

    cur = conn.execute(
        "DELETE FROM proactive_preference_signals WHERE signal_type = ?",
        (signal_type,),
    )
    deleted = cur.rowcount
    conn.commit()

    model = rebuild_preference_snapshot(conn)
    total_after = conn.execute(
        "SELECT COUNT(*) FROM proactive_preference_signals"
    ).fetchone()[0]
    return {
        "total_before": int(total_before),
        "target_rows": int(target_rows),
        "deleted": int(deleted),
        "total_after": int(total_after),
        "model_signals_after": int(model.get("meta", {}).get("total_signals_processed", 0)),
        "dry_run": 0,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=None, help="Path to activity_state.db (default: get_activity_db_path())")
    p.add_argument("--signal-type", default=DEFAULT_SIGNAL_TYPE, help=f"signal_type to delete (default: {DEFAULT_SIGNAL_TYPE})")
    p.add_argument("--commit", action="store_true", help="Actually delete (default: dry-run)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    db_path = args.db or str(get_activity_db_path())
    logger.info("Opening activity DB at %s", db_path)
    with connect_runtime_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        report = detox(conn, signal_type=args.signal_type, commit=args.commit)

    mode = "DRY-RUN" if not args.commit else "COMMITTED"
    logger.info(
        "%s | signals_before=%d target=%d deleted=%d signals_after=%d model_processed_after=%d",
        mode,
        report["total_before"],
        report["target_rows"],
        report["deleted"],
        report["total_after"],
        report["model_signals_after"],
    )
    if not args.commit and report["target_rows"] > 0:
        logger.info("Re-run with --commit to actually delete %d rows.", report["target_rows"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
