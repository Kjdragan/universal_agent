#!/usr/bin/env python3
"""CLI entry point for the guarded Cody-demo scratch reclaim.

Invocation target for a cron-registered system task (see
``gateway_server._register_system_cron_job``). Default behavior is a DRY RUN:
it reports what it WOULD strip without deleting anything. Flip
``UA_CODY_DEMO_CLEANUP_DRY_RUN=0`` (or pass ``--no-dry-run``) only after
reviewing a dry pass.

Examples
--------
    # Dry run over the default scratch root (no deletes):
    python -m universal_agent.scripts.cody_demo_cleanup

    # Live reclaim, custom root:
    python -m universal_agent.scripts.cody_demo_cleanup --no-dry-run \
        --root /opt/universal_agent/AGENT_RUN_WORKSPACES/vp_coder_primary_external
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys

from universal_agent.services.cody_demo_cleanup import (
    DEFAULT_LEAVES,
    DEFAULT_MIN_AGE_HOURS,
    ENV_DEMOS_ROOT,
    ENV_LEAVES,
    ENV_MIN_AGE_HOURS,
    ENV_ROOT,
    ENV_VAULT_ROOT,
    default_demos_root,
    default_scratch_root,
    default_vault_root,
    parse_leaves,
    reclaim_coder_mission_workspaces,
)

logger = logging.getLogger("cody_demo_cleanup")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cody_demo_cleanup",
        description="Guarded reclaim of node_modules/.git from vault-attached Cody mission scratch.",
    )
    p.add_argument(
        "--root",
        default=None,
        help=f"Scratch root (default: ${{{ENV_ROOT}}} or {default_scratch_root()})",
    )
    p.add_argument(
        "--demos-root",
        default=None,
        help=f"Durable demos root (default: ${{{ENV_DEMOS_ROOT}}} or {default_demos_root()})",
    )
    p.add_argument(
        "--vault-root",
        default=None,
        help=f"Vault root (default: ${{{ENV_VAULT_ROOT}}} or {default_vault_root()})",
    )
    p.add_argument(
        "--leaves",
        default=None,
        help=f"Comma-separated heavy-leaf names (default: {','.join(DEFAULT_LEAVES)})",
    )
    p.add_argument(
        "--min-age-hours",
        type=int,
        default=None,
        help=f"Minimum mission age in hours before reclaim (default: {DEFAULT_MIN_AGE_HOURS})",
    )
    p.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Report only, no deletes (default). Use --no-dry-run to actually reclaim.",
    )
    p.add_argument("--disabled", action="store_true", help="Exit as a no-op (feature off).")
    p.add_argument("--json", dest="as_json", action="store_true", help="Print the full report as JSON.")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    leaves = parse_leaves(args.leaves) if args.leaves else parse_leaves(os.getenv(ENV_LEAVES))
    min_age = args.min_age_hours if args.min_age_hours is not None else None
    # CLI defaults to a DRY RUN (safe). --no-dry-run opts into live reclaim.
    dry_run = args.dry_run

    summary = reclaim_coder_mission_workspaces(
        root=Path(args.root) if args.root else None,
        demos_root=Path(args.demos_root) if args.demos_root else None,
        vault_root=Path(args.vault_root) if args.vault_root else None,
        leaves=leaves,
        min_age_hours=min_age,
        dry_run=dry_run,
        enabled=not args.disabled,
    )

    if args.as_json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        enabled = summary.get("enabled")
        if not enabled:
            print("cody_demo_cleanup: disabled (UA_CODY_DEMO_CLEANUP_ENABLED=0 or --disabled)")
            return 0
        dry = summary.get("dry_run")
        mode = "DRY RUN" if dry else "LIVE"
        scanned = summary.get("scanned", 0)
        by_action = summary.get("by_action", {})
        by_reason = summary.get("by_reason", {})
        bytes_freed = int(summary.get("bytes_freed", 0) or 0)
        print(f"cody_demo_cleanup [{mode}] scanned={scanned} bytes_would_free={bytes_freed}")
        print(f"  by_action={by_action}")
        print(f"  by_reason={by_reason}")
        if not dry:
            print("  leaves were DELETED from vault-attached mission scratch")

    # Non-zero exit only on hard errors in the run; skips/dry-runs are success.
    return 0


if __name__ == "__main__":
    sys.exit(main())
