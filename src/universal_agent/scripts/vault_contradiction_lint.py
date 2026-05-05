"""CLI for the vault contradictions sweep (PR 13).

Designed to be run on a monthly cron schedule against every enabled lane.
Report-only — never modifies pages.

Usage:
    PYTHONPATH=src uv run python -m universal_agent.scripts.vault_contradiction_lint
    # Or for a single lane:
    PYTHONPATH=src uv run python -m universal_agent.scripts.vault_contradiction_lint --lane claude-code-intelligence

Exit codes:
    0 — sweep completed (regardless of finding count)
    2 — programmer error (lane unknown, vault root missing)

See docs/proactive_signals/claudedevs_intel_v2_design.md §4.3.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.intel_lanes import (
    CLAUDE_CODE_LANE_KEY,
    enabled_lanes,
    get_lane,
)
from universal_agent.services.vault_lint_contradictions import (
    ContradictionReport,
    run_contradiction_sweep,
    write_contradiction_report,
)

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lane",
        default="",
        help=(
            "Lane slug to sweep. Default: every enabled lane. Use 'all' to "
            "explicitly sweep every enabled lane, or pass a specific slug."
        ),
    )
    parser.add_argument(
        "--profile",
        default="",
        help="Deployment profile for Infisical secret loading.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print the report JSON to stdout without persisting to vault/lint/.",
    )
    return parser.parse_args()


def _vault_path_for_lane(lane_slug: str) -> Path:
    lane = get_lane(lane_slug)
    return resolve_artifacts_dir() / "knowledge-vaults" / lane.vault_slug


def _sweep_one(lane_slug: str, *, write: bool) -> dict[str, Any]:
    vault_path = _vault_path_for_lane(lane_slug)
    report: ContradictionReport = run_contradiction_sweep(vault_path)
    payload = report.to_dict()
    payload["lane_slug"] = lane_slug
    payload["report_path"] = ""
    if write:
        target = write_contradiction_report(vault_path, report)
        payload["report_path"] = str(target)
    return payload


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_runtime_secrets(profile=args.profile or None)

    write = not args.no_write
    selected = (args.lane or "").strip().lower()

    results: list[dict[str, Any]] = []
    if not selected or selected == "all":
        lanes = enabled_lanes()
        if not lanes:
            print(
                json.dumps(
                    {"ok": False, "reason": "no_enabled_lanes", "results": []},
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 2
        for slug in lanes:
            try:
                results.append(_sweep_one(slug, write=write))
            except Exception as exc:
                logger.exception("contradiction sweep raised for lane %s", slug)
                results.append(
                    {
                        "lane_slug": slug,
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
    else:
        try:
            results.append(_sweep_one(selected or CLAUDE_CODE_LANE_KEY, write=write))
        except KeyError as exc:
            print(
                json.dumps({"ok": False, "reason": f"lane_unknown: {exc}"}, indent=2),
                file=sys.stderr,
            )
            return 2

    print(json.dumps({"ok": True, "results": results}, indent=2, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
