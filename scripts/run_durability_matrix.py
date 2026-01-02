#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys
from typing import Optional


RUN_ID_RE = re.compile(r"Run ID:\s*([a-f0-9-]+)", re.IGNORECASE)
RESUME_CMD_RE = re.compile(r"Resume Command:\s*(.+)")


def _run_and_capture(cmd: list[str], env: dict[str, str]) -> tuple[Optional[str], Optional[str], int]:
    print(f"== Running: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    run_id = None
    resume_cmd = None
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
        if run_id is None:
            match = RUN_ID_RE.search(line)
            if match:
                run_id = match.group(1)
        if resume_cmd is None:
            match = RESUME_CMD_RE.search(line)
            if match:
                resume_cmd = match.group(1).strip()
    return run_id, resume_cmd, proc.wait()


def _build_report(
    run_id: Optional[str],
    resume_cmd: Optional[str],
    crash_tool: Optional[str],
    crash_tool_call_id: Optional[str],
    crash_stage: Optional[str],
) -> str:
    return f"""# Durability Matrix Run Report

## Run Info
- run_id: {run_id or "<RUN_ID>"}
- resume_cmd: {resume_cmd or "<RESUME_COMMAND>"}
- crash_trigger:
  - UA_TEST_CRASH_AFTER_TOOL: {crash_tool or "<unset>"}
  - UA_TEST_CRASH_AFTER_TOOL_CALL_ID: {crash_tool_call_id or "<unset>"}
  - UA_TEST_CRASH_STAGE: {crash_stage or "<unset>"}

## Expected Outcomes
- Kill Point A (Task running): previous Task becomes abandoned_on_resume; new Task relaunched; run completes.
- Kill Point B (email send): no duplicate email; ledger dedupe/idempotency prevents re-send.

## Observations
- abandoned tools:
- relaunched tasks:
- side effects (email/upload):

## SQL Queries
```sql
-- Abandoned tools
SELECT tool_call_id, tool_name, status, replay_status, error_detail
FROM tool_calls
WHERE run_id = '{run_id or "<RUN_ID>"}' AND status = 'abandoned_on_resume'
ORDER BY updated_at DESC;
```

```sql
-- Replay outcomes
SELECT tool_call_id, tool_name, replay_status
FROM tool_calls
WHERE run_id = '{run_id or "<RUN_ID>"}' AND replay_status IS NOT NULL
ORDER BY updated_at DESC;
```

```sql
-- Side-effect count (no duplicates expected)
SELECT tool_name, COUNT(*) AS count
FROM tool_calls
WHERE run_id = '{run_id or "<RUN_ID>"}'
  AND status = 'succeeded'
  AND side_effect_class != 'read_only'
GROUP BY tool_name;
```

```sql
-- Duplicate idempotency keys (should be empty)
SELECT tool_name, idempotency_key, COUNT(*) AS count
FROM tool_calls
WHERE run_id = '{run_id or "<RUN_ID>"}'
GROUP BY tool_name, idempotency_key
HAVING COUNT(*) > 1;
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run durability matrix job with crash hooks.")
    parser.add_argument(
        "--job",
        default="tmp/relaunch_resume_job.json",
        help="Path to job spec (default: tmp/relaunch_resume_job.json)",
    )
    parser.add_argument(
        "--crash-tool",
        dest="crash_tool",
        help="Set UA_TEST_CRASH_AFTER_TOOL",
    )
    parser.add_argument(
        "--crash-tool-call-id",
        dest="crash_tool_call_id",
        help="Set UA_TEST_CRASH_AFTER_TOOL_CALL_ID",
    )
    parser.add_argument(
        "--crash-stage",
        dest="crash_stage",
        help="Set UA_TEST_CRASH_STAGE",
    )
    parser.add_argument(
        "--resume-once",
        action="store_true",
        help="Auto-run resume once after the job exits.",
    )
    parser.add_argument(
        "--keep-crash-env",
        action="store_true",
        help="Keep crash env vars for the resume run.",
    )
    parser.add_argument(
        "--report-path",
        help="Write a markdown report template to this path.",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    if args.crash_tool:
        env["UA_TEST_CRASH_AFTER_TOOL"] = args.crash_tool
    if args.crash_tool_call_id:
        env["UA_TEST_CRASH_AFTER_TOOL_CALL_ID"] = args.crash_tool_call_id
    if args.crash_stage:
        env["UA_TEST_CRASH_STAGE"] = args.crash_stage

    run_cmd = [
        "uv",
        "run",
        "python",
        "-m",
        "universal_agent.main",
        "--job",
        args.job,
    ]
    run_id, resume_cmd, exit_code = _run_and_capture(run_cmd, env)

    if args.resume_once:
        if resume_cmd:
            resume_cmd_parts = resume_cmd.split()
        elif run_id:
            resume_cmd_parts = [
                "uv",
                "run",
                "python",
                "src/universal_agent/main.py",
                "--resume",
                "--run-id",
                run_id,
            ]
        else:
            print("No run_id/resume command detected; cannot auto-resume.")
            resume_cmd_parts = None
        if resume_cmd_parts:
            resume_env = env.copy()
            if not args.keep_crash_env:
                resume_env.pop("UA_TEST_CRASH_AFTER_TOOL", None)
                resume_env.pop("UA_TEST_CRASH_AFTER_TOOL_CALL_ID", None)
                resume_env.pop("UA_TEST_CRASH_STAGE", None)
            _run_and_capture(resume_cmd_parts, resume_env)

    report = _build_report(
        run_id=run_id,
        resume_cmd=resume_cmd,
        crash_tool=args.crash_tool,
        crash_tool_call_id=args.crash_tool_call_id,
        crash_stage=args.crash_stage,
    )
    if args.report_path:
        with open(args.report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nReport template written to: {args.report_path}")
    else:
        print("\n" + report)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
