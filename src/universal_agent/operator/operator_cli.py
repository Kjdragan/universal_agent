import argparse
import json
import os
import time
from collections import deque
from typing import Iterable, Optional

from .operator_db import (
    get_last_checkpoint,
    get_run,
    list_runs,
    list_receipt_tool_calls,
    list_tool_calls,
    policy_counts_by_side_effect,
    policy_input_variance,
    policy_unknown_tools,
    request_cancel,
    tail_tool_calls,
)


def _format_workspace(run: dict) -> str:
    workspace_dir = run.get("workspace_dir") or ""
    return workspace_dir if workspace_dir else "N/A"


def _format_tool_call(row: dict) -> str:
    raw = row.get("raw_tool_name") or ""
    name = row.get("tool_name") or ""
    namespace = row.get("tool_namespace") or ""
    tool_label = raw or f"{namespace}:{name}"
    status = row.get("status") or ""
    replay_policy = row.get("replay_policy") or ""
    ts = row.get("created_at") or ""
    idem = row.get("idempotency_key") or ""
    return f"{ts} | {tool_label} | {status} | {replay_policy} | {idem}"


def _read_last_lines(path: str, lines: int) -> list[str]:
    if lines <= 0:
        return []
    queue: deque[str] = deque(maxlen=lines)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                queue.append(line.rstrip("\n"))
    except FileNotFoundError:
        return []
    return list(queue)


def _collect_external_ids(payload: object, keys: set[str], found: dict[str, set[str]]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and value is not None and value != "":
                found.setdefault(key, set()).add(str(value))
            _collect_external_ids(value, keys, found)
    elif isinstance(payload, list):
        for item in payload:
            _collect_external_ids(item, keys, found)


def _extract_external_ids(response_ref: Optional[str]) -> dict[str, list[str]]:
    if not response_ref:
        return {}
    try:
        parsed = json.loads(response_ref)
    except json.JSONDecodeError:
        return {}
    keys = {
        "id",
        "message_id",
        "threadId",
        "thread_id",
        "s3key",
        "request_id",
        "log_id",
    }
    found: dict[str, set[str]] = {}
    _collect_external_ids(parsed, keys, found)
    return {key: sorted(values) for key, values in found.items()}


def _tail_file(path: str, lines: int, follow: bool, poll: float) -> None:
    recent_lines = _read_last_lines(path, lines)
    if not recent_lines:
        print(f"⚠️ No log data available at {path}")
    else:
        for line in recent_lines:
            print(line)
    if not follow:
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            while True:
                line = handle.readline()
                if line:
                    print(line.rstrip("\n"))
                else:
                    time.sleep(poll)
    except FileNotFoundError:
        print(f"⚠️ Log file not found: {path}")


def _tail_db(run_id: str, lines: int, follow: bool, poll: float) -> None:
    seen: set[str] = set()
    rows = tail_tool_calls(run_id, lines)
    if not rows:
        print("⚠️ No tool calls found for run.")
    else:
        for row in rows:
            seen.add(row["tool_call_id"])
            print(_format_tool_call(row))
    if not follow:
        return
    while True:
        rows = tail_tool_calls(run_id, lines)
        for row in rows:
            tool_call_id = row["tool_call_id"]
            if tool_call_id in seen:
                continue
            seen.add(tool_call_id)
            print(_format_tool_call(row))
        time.sleep(poll)


def _cmd_runs_list(statuses: Optional[Iterable[str]], limit: int) -> None:
    runs = list_runs(statuses=statuses, limit=limit)
    if not runs:
        print("No runs found.")
        return
    header = f"{'RUN_ID':36} {'STATUS':16} {'MODE':10} {'CREATED_AT':25} WORKSPACE"
    print(header)
    print("-" * len(header))
    for run in runs:
        run_id = run["run_id"]
        status = run.get("status") or ""
        run_mode = run.get("run_mode") or ""
        created_at = run.get("created_at") or ""
        workspace_dir = _format_workspace(run)
        print(f"{run_id:36} {status:16} {run_mode:10} {created_at:25} {workspace_dir}")


def _cmd_runs_show(run_id: str) -> None:
    run = get_run(run_id)
    if not run:
        print(f"Run not found: {run_id}")
        return
    print(f"Run ID: {run['run_id']}")
    print(f"Status: {run.get('status')}")
    print(f"Created At: {run.get('created_at')}")
    print(f"Updated At: {run.get('updated_at')}")
    print(f"Entrypoint: {run.get('entrypoint')}")
    print(f"Run Mode: {run.get('run_mode')}")
    print(f"Job Path: {run.get('job_path')}")
    print(f"Workspace: {_format_workspace(run)}")
    print(f"Provider Session: {run.get('provider_session_id')}")
    print(f"Provider Session Last Seen: {run.get('provider_session_last_seen_at')}")
    print(f"Provider Session Forked From: {run.get('provider_session_forked_from')}")
    print(f"Parent Run ID: {run.get('parent_run_id')}")
    print(f"Last Checkpoint ID: {run.get('last_checkpoint_id')}")
    print(f"Lease Owner: {run.get('lease_owner')}")
    print(f"Lease Expires At: {run.get('lease_expires_at')}")
    print(f"Last Heartbeat At: {run.get('last_heartbeat_at')}")
    if run.get("cancel_requested_at") or run.get("cancel_reason"):
        print(f"Cancel Requested At: {run.get('cancel_requested_at')}")
        print(f"Cancel Reason: {run.get('cancel_reason')}")
    last_checkpoint = get_last_checkpoint(run_id)
    if last_checkpoint:
        print(
            "Latest Checkpoint: "
            f"{last_checkpoint['checkpoint_id']} @ {last_checkpoint['created_at']} "
            f"({last_checkpoint['checkpoint_type']})"
        )
    last_prompt = run.get("last_job_prompt") or ""
    if last_prompt:
        preview = last_prompt if len(last_prompt) <= 200 else last_prompt[:200] + "..."
        print(f"Last Job Prompt (preview): {preview}")
    tool_calls = list_tool_calls(run_id, limit=10)
    if tool_calls:
        print("\nLast 10 Tool Calls:")
        for row in tool_calls:
            print(f"- {_format_tool_call(row)}")
    else:
        print("\nLast 10 Tool Calls: none")


def _cmd_runs_tail(run_id: str, source: str, follow: bool, lines: int, poll: float) -> None:
    run = get_run(run_id)
    if not run:
        print(f"Run not found: {run_id}")
        return
    workspace_dir = _format_workspace(run)
    if source in ("log", "both"):
        if workspace_dir == "N/A":
            print("⚠️ Workspace not available in run spec; cannot tail log.")
        else:
            run_log_path = os.path.join(workspace_dir, "run.log")
            print(f"==> {run_log_path} <==")
            _tail_file(run_log_path, lines, follow, poll)
    if source in ("db", "both"):
        print("==> tool_calls (DB) <==")
        _tail_db(run_id, lines, follow, poll)


def _cmd_runs_cancel(run_id: str, reason: Optional[str]) -> None:
    ok = request_cancel(run_id, reason)
    if not ok:
        print(f"Run not found: {run_id}")
        return
    print(f"Cancel requested for run {run_id}.")


def _cmd_runs_receipts(run_id: str, output_format: str) -> None:
    rows = list_receipt_tool_calls(run_id)
    receipts = []
    for row in rows:
        external_ids = _extract_external_ids(row.get("response_ref"))
        if row.get("external_correlation_id"):
            external_ids.setdefault("external_correlation_id", []).append(
                str(row["external_correlation_id"])
            )
        receipts.append(
            {
                "tool_call_id": row.get("tool_call_id"),
                "created_at": row.get("created_at"),
                "raw_tool_name": row.get("raw_tool_name"),
                "tool_name": row.get("tool_name"),
                "tool_namespace": row.get("tool_namespace"),
                "side_effect_class": row.get("side_effect_class"),
                "replay_policy": row.get("replay_policy"),
                "status": row.get("status"),
                "idempotency_key": row.get("idempotency_key"),
                "external_ids": external_ids,
            }
        )
    if output_format == "json":
        print(json.dumps(receipts, indent=2))
        return
    print(f"# Run receipts ({run_id})")
    print("")
    if not receipts:
        print("No side-effect receipts found.")
        return
    print("| created_at | tool | status | replay_policy | idempotency_key | external_ids |")
    print("|---|---|---|---|---|---|")
    for receipt in receipts:
        tool_label = receipt.get("raw_tool_name") or f"{receipt.get('tool_namespace')}:{receipt.get('tool_name')}"
        external_parts = []
        for key, values in (receipt.get("external_ids") or {}).items():
            external_parts.append(f"{key}=" + ",".join(values))
        external_text = "; ".join(external_parts) if external_parts else ""
        print(
            f"| {receipt.get('created_at')} | {tool_label} | {receipt.get('status')} | "
            f"{receipt.get('replay_policy')} | {receipt.get('idempotency_key')} | {external_text} |"
        )


def _cmd_policy_audit(output_format: str, limit: int) -> None:
    counts = policy_counts_by_side_effect()
    unknown = policy_unknown_tools(limit=limit)
    variance = policy_input_variance(limit=limit)
    if output_format == "json":
        payload = {
            "counts_by_side_effect": counts,
            "unknown_tools": unknown,
            "input_variance": variance,
        }
        print(json.dumps(payload, indent=2))
        return

    print("# Tool policy audit")
    print("")
    print("## Counts by side_effect_class")
    if not counts:
        print("No tool calls found.")
    else:
        print("| side_effect_class | count |")
        print("|---|---|")
        for row in counts:
            print(f"| {row.get('side_effect_class')} | {row.get('count')} |")

    print("\n## Tools without explicit policy matches")
    if not unknown:
        print("None.")
    else:
        print("| tool_namespace | tool_name | raw_tool_name | count |")
        print("|---|---|---|---|")
        for row in unknown:
            print(
                f"| {row.get('tool_namespace')} | {row.get('tool_name')} | "
                f"{row.get('raw_tool_name') or ''} | {row.get('count')} |"
            )

    print("\n## Tools with input variance (distinct normalized inputs > 1)")
    if not variance:
        print("None.")
    else:
        print("| tool_namespace | tool_name | distinct_inputs | distinct_runs |")
        print("|---|---|---|---|")
        for row in variance:
            print(
                f"| {row.get('tool_namespace')} | {row.get('tool_name')} | "
                f"{row.get('distinct_inputs')} | {row.get('distinct_runs')} |"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ua", description="Universal Agent operator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    runs_parser = subparsers.add_parser("runs", help="Inspect or manage runs")
    runs_sub = runs_parser.add_subparsers(dest="runs_cmd", required=True)

    list_parser = runs_sub.add_parser("list", help="List recent runs")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--status", action="append", default=[])

    show_parser = runs_sub.add_parser("show", help="Show run details")
    show_parser.add_argument("--run-id", required=True)

    tail_parser = runs_sub.add_parser("tail", help="Tail run logs or DB tool calls")
    tail_parser.add_argument("--run-id", required=True)
    tail_parser.add_argument(
        "--source", choices=["log", "db", "both"], default="log"
    )
    tail_parser.add_argument("--follow", action="store_true")
    tail_parser.add_argument("--lines", type=int, default=200)
    tail_parser.add_argument("--poll", type=float, default=1.0)

    cancel_parser = runs_sub.add_parser("cancel", help="Request run cancellation")
    cancel_parser.add_argument("--run-id", required=True)
    cancel_parser.add_argument("--reason", default=None)

    receipts_parser = runs_sub.add_parser("receipts", help="Show side-effect receipt summary")
    receipts_parser.add_argument("--run-id", required=True)
    receipts_parser.add_argument("--format", choices=["md", "json"], default="md")

    policy_parser = subparsers.add_parser("policy", help="Policy audit commands")
    policy_sub = policy_parser.add_subparsers(dest="policy_cmd", required=True)
    audit_parser = policy_sub.add_parser("audit", help="Audit tool policy coverage")
    audit_parser.add_argument("--format", choices=["md", "json"], default="md")
    audit_parser.add_argument("--limit", type=int, default=50)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runs_cmd = getattr(args, "runs_cmd", None)
    if runs_cmd == "list":
        _cmd_runs_list(args.status, args.limit)
        return 0
    if runs_cmd == "show":
        _cmd_runs_show(args.run_id)
        return 0
    if runs_cmd == "tail":
        _cmd_runs_tail(args.run_id, args.source, args.follow, args.lines, args.poll)
        return 0
    if runs_cmd == "cancel":
        _cmd_runs_cancel(args.run_id, args.reason)
        return 0
    if runs_cmd == "receipts":
        _cmd_runs_receipts(args.run_id, args.format)
        return 0
    if args.command == "policy" and args.policy_cmd == "audit":
        _cmd_policy_audit(args.format, args.limit)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
