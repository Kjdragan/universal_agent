"""``python -m universal_agent.dev_tools <subcommand>`` — dev-mode CLI helpers.

See ``__init__.py`` for the subcommand list.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any

from universal_agent.loop_control import (
    _KNOWN_LOOPS,
    explain_loop_decision,
    is_development_runtime,
    report_dev_overrides,
)

# Default workspace location for persisted cron jobs. Mirrors the path
# CronService uses in gateway_server when constructing the cron store.
_DEFAULT_WORKSPACES_DIR = Path("AGENT_RUN_WORKSPACES")
_CRON_JOBS_FILENAME = "cron_jobs.json"


def _cmd_env_report(_args: argparse.Namespace) -> int:
    """``env-report`` subcommand."""
    logger = logging.getLogger("dev_tools.env_report")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not is_development_runtime():
        print(
            "env-report: not in dev mode (UA_RUNTIME_STAGE != 'development'). "
            "Set UA_RUNTIME_STAGE=development to see the per-loop report. "
            "report_dev_overrides() is a no-op outside dev.",
            file=sys.stderr,
        )
        return 2
    report_dev_overrides(log=logger)
    return 0


def _cmd_loop_status(args: argparse.Namespace) -> int:
    """``loop-status <name>`` subcommand."""
    name = args.name.strip().lower()
    if not name:
        print("loop-status: <name> argument required", file=sys.stderr)
        return 2
    msg = explain_loop_decision(name, prod_default=True)
    print(f"loop[{name}]: {msg}")
    if name not in _KNOWN_LOOPS:
        print(
            f"\nNote: {name!r} is not in the canonical _KNOWN_LOOPS list. "
            f"explain_loop_decision is still answering, but if {name!r} isn't "
            f"actually gated by should_run_loop somewhere in the codebase, the "
            f"answer is hypothetical. Known loops: {', '.join(_KNOWN_LOOPS)}",
            file=sys.stderr,
        )
    return 0


def _cmd_cron_list(args: argparse.Namespace) -> int:
    """``cron-list`` subcommand."""
    workspace_dir = Path(args.workspace) if args.workspace else _DEFAULT_WORKSPACES_DIR
    cron_jobs_path = workspace_dir / _CRON_JOBS_FILENAME
    if not cron_jobs_path.exists():
        print(
            f"cron-list: no cron jobs file at {cron_jobs_path}. "
            f"This is normal for a fresh dev environment — only the VPS "
            f"production gateway accumulates persisted jobs.",
        )
        return 0
    try:
        payload = json.loads(cron_jobs_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"cron-list: failed to parse {cron_jobs_path}: {exc}", file=sys.stderr)
        return 1
    jobs: dict[str, Any]
    if isinstance(payload, dict):
        jobs = payload
    else:
        print(
            f"cron-list: unexpected top-level shape in {cron_jobs_path} "
            f"(expected dict, got {type(payload).__name__}). Refusing to guess.",
            file=sys.stderr,
        )
        return 1
    if not jobs:
        print(f"cron-list: {cron_jobs_path} is empty (no persisted cron jobs).")
        return 0
    print(f"cron-list: {len(jobs)} persisted cron job(s) in {cron_jobs_path}")
    print(f"{'job_id':<22} {'cron_expr/every':<28} {'next_run_at':<22}  command")
    print("-" * 110)
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        cron_expr = job.get("cron_expr") or ""
        every = job.get("every_seconds") or 0
        schedule = cron_expr or (f"every {every}s" if every else "?")
        next_run = job.get("next_run_at") or "?"
        command = job.get("command") or job.get("entrypoint") or "?"
        # Truncate command for display
        if isinstance(command, str) and len(command) > 50:
            command = command[:47] + "..."
        print(f"{job_id:<22} {schedule:<28} {str(next_run):<22}  {command}")
    if is_development_runtime():
        print(
            "\nNote: in dev mode CronService refuses to LOAD these jobs at startup "
            "(see Phase D belt-and-suspenders in cron_service.py). So even if "
            "this file is present, dev runs ignore it.",
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m universal_agent.dev_tools",
        description="Dev-mode CLI helpers for the local-dev workflow.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<subcommand>")

    sub.add_parser(
        "env-report",
        help="Print per-loop dev-mode decisions (same as gateway startup log).",
    )

    p_status = sub.add_parser(
        "loop-status",
        help="Explain why a specific loop is on/off.",
    )
    p_status.add_argument("name", help="Loop name (e.g., heartbeat, cron, agentmail_service).")

    p_cron = sub.add_parser(
        "cron-list",
        help="List persisted cron jobs from AGENT_RUN_WORKSPACES/cron_jobs.json.",
    )
    p_cron.add_argument(
        "--workspace",
        default=None,
        help="Path to AGENT_RUN_WORKSPACES dir (default: ./AGENT_RUN_WORKSPACES).",
    )

    args = parser.parse_args(argv)

    handlers = {
        "env-report": _cmd_env_report,
        "loop-status": _cmd_loop_status,
        "cron-list": _cmd_cron_list,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
