#!/usr/bin/env python3
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


RUN_ID_PATTERN = re.compile(r"Run ID:\s*([0-9a-fA-F-]{36})")
RESUME_CMD_PATTERN = re.compile(r"Resume Command:\s*(.+)")
WORKSPACE_PATTERN = re.compile(r"Injected Session Workspace:\s*(.+)")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _runtime_db_path() -> Path:
    return _repo_root() / "AGENT_RUN_WORKSPACES" / "runtime_state.db"


def _ensure_env(env: dict[str, str]) -> dict[str, str]:
    env.setdefault("PYTHONPATH", "src")
    env.setdefault("UV_CACHE_DIR", str(_repo_root() / ".uv-cache"))
    return env


def _parse_run_metadata(line: str, current: dict[str, str]) -> None:
    if "run_id" not in current:
        match = RUN_ID_PATTERN.search(line)
        if match:
            current["run_id"] = match.group(1)
    if "resume_cmd" not in current:
        match = RESUME_CMD_PATTERN.search(line)
        if match:
            current["resume_cmd"] = match.group(1).strip()
    if "workspace_dir" not in current:
        match = WORKSPACE_PATTERN.search(line)
        if match:
            current["workspace_dir"] = match.group(1).strip()


def _stream_process(cmd: list[str], env: dict[str, str]) -> tuple[int, dict[str, str]]:
    metadata: dict[str, str] = {}
    process = subprocess.Popen(
        cmd,
        cwd=str(_repo_root()),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        _parse_run_metadata(line, metadata)
    process.wait()
    return process.returncode or 0, metadata


def _load_run_from_db(run_id: Optional[str]) -> dict[str, str]:
    db_path = _runtime_db_path()
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if run_id:
            row = conn.execute(
                "SELECT run_id, run_spec_json FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT run_id, run_spec_json FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return {}
        run_spec_json = row["run_spec_json"]
        workspace_dir = ""
        try:
            run_spec = json.loads(run_spec_json)
            workspace_dir = run_spec.get("workspace_dir") or ""
        except json.JSONDecodeError:
            workspace_dir = ""
        return {"run_id": row["run_id"], "workspace_dir": workspace_dir}
    finally:
        conn.close()


def _query_duplicates(run_id: str) -> list[tuple[str, int]]:
    db_path = _runtime_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT idempotency_key, COUNT(*) AS count
            FROM tool_calls
            WHERE run_id = ?
            GROUP BY idempotency_key
            HAVING COUNT(*) > 1
            """,
            (run_id,),
        ).fetchall()
        return [(row["idempotency_key"], row["count"]) for row in rows]
    finally:
        conn.close()


def _query_side_effect_counts(run_id: str) -> list[tuple[str, int]]:
    db_path = _runtime_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT tool_name, COUNT(*) AS count
            FROM tool_calls
            WHERE run_id = ?
              AND status = 'succeeded'
              AND side_effect_class != 'read_only'
            GROUP BY tool_name
            ORDER BY count DESC
            """,
            (run_id,),
        ).fetchall()
        return [(row["tool_name"], row["count"]) for row in rows]
    finally:
        conn.close()


def _verify_artifacts(run_id: str, workspace_dir: str, expected: list[str]) -> bool:
    ok = True
    if not workspace_dir:
        print("❌ No workspace_dir found for run.")
        return False
    if not os.path.isdir(workspace_dir):
        print(f"❌ Workspace directory missing: {workspace_dir}")
        return False
    expected_paths = expected[:]
    if not expected_paths:
        expected_paths.append(f"job_completion_{run_id}.md")
        expected_paths.append("work_products")
    for rel_path in expected_paths:
        path = rel_path if os.path.isabs(rel_path) else os.path.join(workspace_dir, rel_path)
        if not os.path.exists(path):
            ok = False
            print(f"❌ Missing artifact: {path}")
        else:
            print(f"✅ Found artifact: {path}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Durability smoke test runner")
    parser.add_argument("--job", default="tmp/quick_resume_job.json")
    parser.add_argument("--no-resume", action="store_true", help="Skip auto resume")
    parser.add_argument("--resume-always", action="store_true", help="Always resume once")
    parser.add_argument("--expected-artifact", action="append", default=[])
    parser.add_argument("--crash-after-tool", default=None)
    parser.add_argument("--crash-after-tool-call-id", default=None)
    parser.add_argument("--crash-stage", default=None)
    parser.add_argument("--email-to", default=None)
    args = parser.parse_args()

    job_path = Path(args.job)
    if not job_path.is_absolute():
        job_path = _repo_root() / job_path
    if not job_path.exists():
        print(f"❌ Job file not found: {job_path}")
        return 1
    if "relaunch" in job_path.name and not args.expected_artifact:
        print(
            "ℹ️ Relaunch job selected. Consider passing --expected-artifact "
            "for work_products/relaunch_report.html and work_products/relaunch_report.pdf "
            "to enforce full artifact validation."
        )

    env = _ensure_env(os.environ.copy())
    if args.crash_after_tool:
        env["UA_TEST_CRASH_AFTER_TOOL"] = args.crash_after_tool
    if args.crash_after_tool_call_id:
        env["UA_TEST_CRASH_AFTER_TOOL_CALL_ID"] = args.crash_after_tool_call_id
    if args.crash_stage:
        env["UA_TEST_CRASH_STAGE"] = args.crash_stage
    if args.email_to:
        env["UA_TEST_EMAIL_TO"] = args.email_to

    start_cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "universal_agent.main",
        "--job",
        str(job_path),
    ]
    print("▶️ Starting job...")
    exit_code, metadata = _stream_process(start_cmd, env)

    if "run_id" not in metadata:
        fallback = _load_run_from_db(None)
        metadata.update(fallback)
    run_id = metadata.get("run_id")
    if not run_id:
        print("❌ Could not determine run_id from output or DB.")
        return 1

    workspace_dir = metadata.get("workspace_dir")
    resume_cmd = metadata.get("resume_cmd")
    if not resume_cmd:
        resume_cmd = (
            f"PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id {run_id}"
        )

    print(f"\nRun ID: {run_id}")
    print(f"Resume Command: {resume_cmd}")

    crash_requested = bool(
        args.crash_after_tool or args.crash_after_tool_call_id or args.crash_stage
    )
    should_resume = not args.no_resume and (args.resume_always or crash_requested or exit_code != 0)

    if should_resume:
        resume_env = _ensure_env(os.environ.copy())
        if args.email_to:
            resume_env["UA_TEST_EMAIL_TO"] = args.email_to
        print("\n▶️ Resuming run...")
        resume_cmd_list = [
            "uv",
            "run",
            "python",
            "-m",
            "universal_agent.main",
            "--resume",
            "--run-id",
            run_id,
        ]
        _stream_process(resume_cmd_list, resume_env)

    workspace_dir = workspace_dir or _load_run_from_db(run_id).get("workspace_dir", "")
    print("\n🔎 Verifying artifacts...")
    artifacts_ok = _verify_artifacts(run_id, workspace_dir or "", args.expected_artifact)

    print("\n🔎 Verifying DB invariants...")
    duplicates = _query_duplicates(run_id)
    if duplicates:
        print("❌ Duplicate idempotency keys detected:")
        for key, count in duplicates:
            print(f"- {key} ({count})")
    else:
        print("✅ No duplicate idempotency keys detected.")

    side_effect_counts = _query_side_effect_counts(run_id)
    if side_effect_counts:
        print("Side-effect tool counts:")
        for tool_name, count in side_effect_counts:
            print(f"- {tool_name}: {count}")

    if duplicates or not artifacts_ok:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
