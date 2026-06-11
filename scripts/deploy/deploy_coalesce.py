#!/usr/bin/env python3
"""Decide whether the current Deploy run is redundant and should be skipped.

Phase B of the deploy-restart resilience ADR
(project_docs/06_platform/12_deploy_restart_resilience_adr.md).
"""

from __future__ import annotations

import argparse
import json
import sys

# Run states that are still pending or executing — a newer run in one of these
# WILL deploy origin/main HEAD after us (the deploy concurrency group serializes
# in id order), so it supersedes us. A "completed" newer run is not counted: it
# won't run again, so skipping on it could leave HEAD unshipped.
ACTIVE_STATUSES = frozenset(
    {"queued", "in_progress", "waiting", "requested", "pending", "action_required"}
)


def should_skip_redundant_deploy(runs, my_run_id):
    """Return (skip, reason) for the current Deploy run.

    `runs` is a list of dicts with `databaseId` and `status`. Skip iff a
    strictly-newer Deploy run (higher monotonic run id) is still active.
    """
    newer = [
        r
        for r in runs
        if int(r["databaseId"]) > int(my_run_id)
        and str(r.get("status") or "").strip() in ACTIVE_STATUSES
    ]
    if newer:
        ids = sorted(int(r["databaseId"]) for r in newer)
        return True, f"superseded by newer active deploy run(s): {ids}"
    return False, "no newer active deploy run; proceeding"


def _decide_from_stdin(stdin_text, my_run_id):
    """Parse `gh run list --json ...` output and decide. Fail-safe: any parse
    error or unexpected shape returns (False, ...) so a deploy is NEVER skipped
    on bad data."""
    try:
        runs = json.loads(stdin_text or "[]")
        if not isinstance(runs, list):
            return False, "runs payload not a list; proceeding (fail-safe)"
        return should_skip_redundant_deploy(runs, my_run_id)
    except Exception as exc:  # noqa: BLE001 — never let a parse error skip a deploy
        return False, f"could not evaluate coalescing ({exc}); proceeding (fail-safe)"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Decide whether to skip a redundant Deploy run.")
    parser.add_argument("--my-run-id", required=True, help="github.run_id of the current run")
    args = parser.parse_args(argv)

    try:
        my_run_id = int(str(args.my_run_id).strip())
    except (TypeError, ValueError):
        print("skip=false")
        print("invalid --my-run-id; proceeding (fail-safe)", file=sys.stderr)
        return 0

    skip, reason = _decide_from_stdin(sys.stdin.read(), my_run_id)
    # stdout line is consumed via `>> $GITHUB_OUTPUT`; reason goes to the log.
    print("skip=true" if skip else "skip=false")
    print(reason, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
