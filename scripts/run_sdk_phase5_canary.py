#!/usr/bin/env python3
"""Run a fast canary matrix for the Claude Agent SDK 0.1.48 rollout.

This script validates Phase 5 promotion gates by exercising each feature gate in
progressive order. It writes both JSON and Markdown summaries so rollout
operators have a durable go/no-go artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class CanaryCheck:
    name: str
    phase: str
    description: str
    command: list[str]
    env_overrides: dict[str, str]
    required: bool = True


@dataclass
class CanaryResult:
    name: str
    phase: str
    required: bool
    status: str
    exit_code: int
    duration_seconds: float
    command: str
    env: dict[str, str]


def _run_streaming(command: list[str], env: dict[str, str], cwd: Path) -> tuple[int, float]:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")

    exit_code = process.wait()
    duration = time.monotonic() - started
    return exit_code, duration


def _flags_env(typed: str, history: str, dynamic_mcp: str) -> dict[str, str]:
    return {
        "UA_ENABLE_SDK_TYPED_TASK_EVENTS": typed,
        "UA_ENABLE_SDK_SESSION_HISTORY": history,
        "UA_ENABLE_DYNAMIC_MCP": dynamic_mcp,
    }


def _build_checks(profile: str, include_live_probe: bool) -> list[CanaryCheck]:
    checks: list[CanaryCheck] = []

    checks.append(
        CanaryCheck(
            name="phase1_sdk_runtime_preflight",
            phase="phase1",
            description="Ensure installed Claude Agent SDK is >= 0.1.48 and banner helpers resolve.",
            command=[
                "uv",
                "run",
                "python",
                "-c",
                (
                    "import sys; sys.path.insert(0, 'src'); "
                    "from universal_agent.sdk.runtime_info import "
                    "read_sdk_runtime_info, sdk_version_is_at_least; "
                    "info=read_sdk_runtime_info(); "
                    "print(f'sdk={info.sdk_version} bundled_cli={info.bundled_cli_version}'); "
                    "raise SystemExit(0 if sdk_version_is_at_least('0.1.48', current=info.sdk_version) else 2)"
                ),
            ],
            env_overrides=_flags_env("0", "0", "0"),
            required=True,
        )
    )

    checks.append(
        CanaryCheck(
            name="phase2_typed_events_and_stop_reason",
            phase="phase2",
            description="Validate typed task lifecycle parsing + stop_reason + hook attribution.",
            command=[
                "uv",
                "run",
                "pytest",
                "-q",
                "tests/unit/test_sdk_task_events.py",
                "tests/unit/test_sdk_result_telemetry.py",
                "tests/unit/test_hooks_sdk_agent_identity.py",
                "tests/unit/test_sdk_feature_flags.py",
            ],
            env_overrides=_flags_env("1", "0", "0"),
            required=True,
        )
    )

    checks.append(
        CanaryCheck(
            name="phase3_session_history_adapter",
            phase="phase3",
            description="Validate SDK session history adapter and gateway augmentation path.",
            command=[
                "uv",
                "run",
                "pytest",
                "-q",
                "tests/unit/test_sdk_session_history_adapter.py",
                "tests/unit/test_gateway_sdk_history_flag.py",
            ],
            env_overrides=_flags_env("1", "1", "0"),
            required=True,
        )
    )

    checks.append(
        CanaryCheck(
            name="phase4_dynamic_mcp_admin_controls",
            phase="phase4",
            description="Validate guarded dynamic MCP add/remove/status APIs and validation.",
            command=[
                "uv",
                "run",
                "pytest",
                "-q",
                "tests/gateway/test_dynamic_mcp_ops.py",
            ],
            env_overrides=_flags_env("1", "1", "1"),
            required=True,
        )
    )

    checks.append(
        CanaryCheck(
            name="full_flags_regression_slice",
            phase="phase5",
            description="Run a compatibility slice with all rollout flags enabled.",
            command=[
                "uv",
                "run",
                "pytest",
                "-q",
                "tests/gateway/test_gateway.py",
                "tests/unit/test_tool_schema_guardrail.py",
                "tests/unit/test_task_guardrails.py",
                "tests/test_hooks_workspace_guard.py",
            ],
            env_overrides=_flags_env("1", "1", "1"),
            required=True,
        )
    )

    if profile == "full":
        checks.append(
            CanaryCheck(
                name="full_flags_integration_slice",
                phase="phase5",
                description="Deeper integration checks under all rollout flags.",
                command=[
                    "uv",
                    "run",
                    "pytest",
                    "-q",
                    "tests/gateway/test_gateway_integration.py",
                    "tests/reproduction/test_session_persistence.py",
                ],
                env_overrides=_flags_env("1", "1", "1"),
                required=True,
            )
        )

    if include_live_probe:
        checks.append(
            CanaryCheck(
                name="live_session_continuity_probe",
                phase="phase5",
                description="Optional live smoke probe for session continuity behavior.",
                command=["uv", "run", "python", "scripts/session_continuity_probe.py"],
                env_overrides=_flags_env("1", "1", "1"),
                required=False,
            )
        )

    return checks


def _format_markdown(
    *,
    started_at: str,
    finished_at: str,
    profile: str,
    results: Iterable[CanaryResult],
    overall_status: str,
) -> str:
    rows = list(results)
    required_failures = [row for row in rows if row.required and row.status != "pass"]
    optional_failures = [row for row in rows if (not row.required) and row.status != "pass"]

    lines: list[str] = []
    lines.append("# SDK Phase 5 Canary Report")
    lines.append("")
    lines.append(f"- started_at_utc: {started_at}")
    lines.append(f"- finished_at_utc: {finished_at}")
    lines.append(f"- profile: {profile}")
    lines.append(f"- overall_status: {overall_status}")
    lines.append(f"- required_failures: {len(required_failures)}")
    lines.append(f"- optional_failures: {len(optional_failures)}")
    lines.append("")
    lines.append("| Check | Phase | Required | Status | Duration (s) | Exit |")
    lines.append("|---|---|---:|---|---:|---:|")
    for row in rows:
        lines.append(
            f"| {row.name} | {row.phase} | {'yes' if row.required else 'no'} | "
            f"{row.status} | {row.duration_seconds:.2f} | {row.exit_code} |"
        )

    lines.append("")
    lines.append("## Promotion Decision")
    if required_failures:
        lines.append("NO-GO: at least one required canary check failed.")
    else:
        lines.append("GO: all required canary checks passed.")

    if optional_failures:
        lines.append("")
        lines.append("## Optional Check Failures")
        for row in optional_failures:
            lines.append(f"- {row.name} (exit_code={row.exit_code})")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run accelerated Phase 5 SDK canary checks.")
    parser.add_argument(
        "--profile",
        choices=("quick", "full"),
        default="quick",
        help="quick: fast signal; full: adds integration coverage",
    )
    parser.add_argument(
        "--out-dir",
        default="artifacts/sdk_phase5_canary",
        help="Directory for JSON/Markdown reports.",
    )
    parser.add_argument(
        "--include-live-probe",
        action="store_true",
        help="Include optional live session continuity probe.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue after required check failures.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = (repo_root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    checks = _build_checks(profile=args.profile, include_live_probe=args.include_live_probe)

    started_at = datetime.now(timezone.utc).isoformat()
    results: list[CanaryResult] = []

    for index, check in enumerate(checks, start=1):
        print("\n" + "=" * 88)
        print(f"[{index}/{len(checks)}] {check.name} ({check.phase})")
        print(check.description)
        print(f"Command: {' '.join(check.command)}")

        env = os.environ.copy()
        env.update(check.env_overrides)
        env.setdefault("PYTHONUNBUFFERED", "1")

        print(
            "Flags: "
            f"typed={env.get('UA_ENABLE_SDK_TYPED_TASK_EVENTS')} "
            f"history={env.get('UA_ENABLE_SDK_SESSION_HISTORY')} "
            f"dynamic_mcp={env.get('UA_ENABLE_DYNAMIC_MCP')}"
        )

        exit_code, duration = _run_streaming(check.command, env, repo_root)
        status = "pass" if exit_code == 0 else "fail"
        result = CanaryResult(
            name=check.name,
            phase=check.phase,
            required=check.required,
            status=status,
            exit_code=exit_code,
            duration_seconds=duration,
            command=" ".join(check.command),
            env=dict(check.env_overrides),
        )
        results.append(result)

        if status == "fail" and check.required and not args.keep_going:
            print("\nStopping early because a required check failed. Use --keep-going to continue.")
            break

    finished_at = datetime.now(timezone.utc).isoformat()
    required_failures = [row for row in results if row.required and row.status != "pass"]
    overall_status = "pass" if not required_failures else "fail"

    payload = {
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "profile": args.profile,
        "overall_status": overall_status,
        "required_failures": len(required_failures),
        "checks": [asdict(row) for row in results],
    }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"sdk_phase5_canary_{timestamp}.json"
    latest_json_path = out_dir / "latest.json"
    md_path = out_dir / f"sdk_phase5_canary_{timestamp}.md"
    latest_md_path = out_dir / "latest.md"

    json_text = json.dumps(payload, indent=2, sort_keys=True)
    json_path.write_text(json_text + "\n", encoding="utf-8")
    latest_json_path.write_text(json_text + "\n", encoding="utf-8")

    md_text = _format_markdown(
        started_at=started_at,
        finished_at=finished_at,
        profile=args.profile,
        results=results,
        overall_status=overall_status,
    )
    md_path.write_text(md_text, encoding="utf-8")
    latest_md_path.write_text(md_text, encoding="utf-8")

    print("\n" + "=" * 88)
    print(f"Overall status: {overall_status}")
    print(f"Report (json): {json_path}")
    print(f"Report (json latest): {latest_json_path}")
    print(f"Report (md): {md_path}")
    print(f"Report (md latest): {latest_md_path}")

    return 0 if overall_status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
