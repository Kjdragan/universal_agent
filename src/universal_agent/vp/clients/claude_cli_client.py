"""Claude Code CLI Client — VP worker client that spawns claude CLI subprocesses.

Launches `claude --print --output-format stream-json` as an external process,
monitors the JSON output stream, handles input requests, and captures results.

This gives VP workers access to Claude Code capabilities (Agent Teams, full
toolchain, skills) that aren't available in the Claude Agent SDK runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import time
from typing import Any, Optional
import uuid

from universal_agent.codebase_policy import (
    is_approved_codebase_path,
    repo_mutation_requested,
)
from universal_agent.feature_flags import vp_handoff_root, vp_hard_block_ua_repo
from universal_agent.guardrails.workspace_guard import (
    WorkspaceGuardError,
    enforce_external_target_path,
)
from universal_agent.vp.clients.base import MissionOutcome, VpClient

# Lazy-import session budget to avoid circular dependencies
_session_budget = None


def _get_budget():
    global _session_budget
    if _session_budget is None:
        from universal_agent.session_budget import SessionBudget
        _session_budget = SessionBudget.get_instance()
    return _session_budget

logger = logging.getLogger(__name__)

# Default timeout for CLI sessions (30 minutes)
DEFAULT_CLI_TIMEOUT_SECONDS = 1800
# Maximum timeout (4 hours)
MAX_CLI_TIMEOUT_SECONDS = 14400
# Stall detection: if no output for this long, consider it stalled
STALL_TIMEOUT_SECONDS = 300
# Maximum retries for failed CLI sessions
MAX_RETRIES = 2


class ClaudeCodeCLIClient(VpClient):
    """VP client that directs Claude Code CLI sessions.

    Instead of running the Claude Agent SDK (ProcessTurnAdapter), this client
    spawns `claude --print` as an external subprocess. The CLI has access to
    Agent Teams, the full Claude Code toolchain, and skills — capabilities
    not available in the SDK runtime.

    The VP worker acts as project director:
    - Crafts a structured prompt from the mission objective
    - Launches the CLI subprocess
    - Monitors the JSON output stream (light touch, not micromanaging)
    - Responds to input requests when the CLI needs guidance
    - Evaluates results on completion
    - Reports back via MissionOutcome
    """

    async def run_mission(
        self,
        *,
        mission: dict[str, Any],
        workspace_root: Path,
    ) -> MissionOutcome:
        mission_id = str(mission.get("mission_id") or "")
        objective = str(mission.get("objective") or "").strip()
        if not objective:
            return MissionOutcome(status="failed", message="missing objective")

        payload = _parse_payload(mission.get("payload_json"))
        timeout_seconds = min(
            max(30, int(payload.get("timeout_seconds") or DEFAULT_CLI_TIMEOUT_SECONDS)),
            MAX_CLI_TIMEOUT_SECONDS,
        )
        max_retries = int(payload.get("max_retries") or MAX_RETRIES)
        skill_name = str(payload.get("skill") or "").strip()
        enable_agent_teams = bool(payload.get("enable_agent_teams", True))

        workspace_dir = _resolve_workspace(mission_id, workspace_root, payload)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Build the prompt for the CLI
        prompt = _build_cli_prompt(objective, payload, workspace_dir, skill_name)

        # Acquire session budget slots
        budget = _get_budget()
        cli_consumer_id = f"cli.{mission_id}" if mission_id else "cli.unknown"
        estimated_slots = 2 if enable_agent_teams else 1  # Agent Teams uses ~2 sessions
        budget_acquired = budget.acquire(
            cli_consumer_id,
            slots=estimated_slots,
            metadata={"mission_id": mission_id, "agent_teams": enable_agent_teams},
        )
        if not budget_acquired:
            logger.warning(
                "CLI mission %s: session budget full (available=%d, needed=%d)",
                mission_id, budget.available(), estimated_slots,
            )
            return MissionOutcome(
                status="failed",
                message=f"session budget exhausted (available={budget.available()}, needed={estimated_slots})",
            )

        # Enter heavy mode if using Agent Teams
        if enable_agent_teams:
            budget.enter_heavy_mode(cli_consumer_id)

        try:
            # Attempt execution with retries
            last_outcome: Optional[MissionOutcome] = None
            for attempt in range(1, max_retries + 2):  # +2 because range is exclusive and attempt 1 is the first try
                logger.info(
                    "CLI mission %s attempt %d/%d workspace=%s",
                    mission_id, attempt, max_retries + 1, workspace_dir,
                )

                current_prompt = prompt
                if attempt > 1 and last_outcome and last_outcome.message:
                    current_prompt = _build_retry_prompt(
                        original_prompt=prompt,
                        previous_error=last_outcome.message,
                        attempt=attempt,
                    )

                outcome = await _execute_cli_session(
                    prompt=current_prompt,
                    workspace_dir=workspace_dir,
                    timeout_seconds=timeout_seconds,
                    enable_agent_teams=enable_agent_teams,
                    mission_id=mission_id,
                )

                if outcome.status == "completed":
                    return outcome

                last_outcome = outcome
                if attempt > max_retries:
                    break

                logger.warning(
                    "CLI mission %s attempt %d failed: %s — retrying",
                    mission_id, attempt, outcome.message,
                )

            return last_outcome or MissionOutcome(
                status="failed",
                result_ref=f"workspace://{workspace_dir}",
                message="CLI execution failed after all retries",
            )
        finally:
            budget.release(cli_consumer_id)
            budget.exit_heavy_mode(cli_consumer_id)


async def _execute_cli_session(
    *,
    prompt: str,
    workspace_dir: Path,
    timeout_seconds: int,
    enable_agent_teams: bool,
    mission_id: str,
) -> MissionOutcome:
    """Spawn a claude CLI subprocess and monitor its output."""

    env = _build_cli_env(enable_agent_teams, workspace_dir)

    cmd = [
        "claude",
        "--print",
        "--output-format", "stream-json",
        "--verbose",
    ]

    logger.info("Launching CLI: %s (cwd=%s, timeout=%ds)", " ".join(cmd), workspace_dir, timeout_seconds)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace_dir),
            env=env,
        )
    except FileNotFoundError:
        return MissionOutcome(
            status="failed",
            result_ref=f"workspace://{workspace_dir}",
            message="claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code",
        )
    except Exception as exc:
        return MissionOutcome(
            status="failed",
            result_ref=f"workspace://{workspace_dir}",
            message=f"Failed to launch claude CLI: {exc}",
        )

    # Send the prompt to stdin
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
    except Exception as exc:
        logger.error("Failed to write prompt to CLI stdin: %s", exc)
        _kill_process(proc)
        return MissionOutcome(
            status="failed",
            result_ref=f"workspace://{workspace_dir}",
            message=f"Failed to send prompt to CLI: {exc}",
        )

    # Monitor output stream
    result = await _monitor_cli_output(
        proc=proc,
        timeout_seconds=timeout_seconds,
        workspace_dir=workspace_dir,
        mission_id=mission_id,
    )

    return result


async def _monitor_cli_output(
    *,
    proc: asyncio.subprocess.Process,
    timeout_seconds: int,
    workspace_dir: Path,
    mission_id: str,
) -> MissionOutcome:
    """Read the CLI's JSON stream output and extract the result."""

    final_text = ""
    cost_info: dict[str, Any] = {}
    tool_calls = 0
    errors: list[str] = []
    last_output_time = time.monotonic()
    stream_lines: list[str] = []

    try:
        deadline = time.monotonic() + timeout_seconds

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("CLI mission %s timed out after %ds", mission_id, timeout_seconds)
                _kill_process(proc)
                return MissionOutcome(
                    status="failed",
                    result_ref=f"workspace://{workspace_dir}",
                    message=f"CLI session timed out after {timeout_seconds}s",
                    payload={"tool_calls": tool_calls, "lines_captured": len(stream_lines)},
                )

            try:
                line_bytes = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=min(remaining, STALL_TIMEOUT_SECONDS),
                )
            except asyncio.TimeoutError:
                # Check if process is still alive
                if proc.returncode is not None:
                    break
                # Stall detection
                stall_duration = time.monotonic() - last_output_time
                if stall_duration > STALL_TIMEOUT_SECONDS:
                    logger.warning(
                        "CLI mission %s stalled (no output for %ds)",
                        mission_id, int(stall_duration),
                    )
                continue

            if not line_bytes:
                # EOF — process finished
                break

            last_output_time = time.monotonic()
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            stream_lines.append(line)

            # Parse JSON events
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = str(event.get("type") or "")

            if event_type == "result":
                # Final result from the CLI
                result_data = event.get("result") or event.get("text") or ""
                if isinstance(result_data, str):
                    final_text = result_data
                elif isinstance(result_data, dict):
                    final_text = str(result_data.get("text") or result_data.get("content") or json.dumps(result_data))
                cost_info = event.get("cost") or event.get("usage") or {}
                duration_ms = event.get("duration_ms") or event.get("duration") or 0
                cost_info["duration_ms"] = duration_ms
                cost_usd = event.get("cost_usd")
                if cost_usd is not None:
                    cost_info["cost_usd"] = float(cost_usd)

            elif event_type in ("assistant", "message"):
                # Intermediate assistant message
                msg = event.get("message") or event
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            final_text = str(block.get("text") or "")

            elif event_type == "tool_use":
                tool_calls += 1

            elif event_type == "error":
                error_msg = str(event.get("error") or event.get("message") or "unknown error")
                errors.append(error_msg)
                logger.warning("CLI mission %s error event: %s", mission_id, error_msg)

    except Exception as exc:
        logger.exception("Error monitoring CLI output for mission %s", mission_id)
        _kill_process(proc)
        return MissionOutcome(
            status="failed",
            result_ref=f"workspace://{workspace_dir}",
            message=f"Error monitoring CLI: {exc}",
        )

    # Wait for process to finish
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        _kill_process(proc)

    # Capture stderr
    stderr_text = ""
    try:
        stderr_bytes = await proc.stderr.read()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
    except Exception:
        pass

    exit_code = proc.returncode or 0

    # Save the stream log for debugging
    _save_stream_log(workspace_dir, stream_lines, stderr_text, exit_code)

    if exit_code != 0 or errors:
        error_summary = "; ".join(errors) if errors else stderr_text or f"CLI exited with code {exit_code}"
        return MissionOutcome(
            status="failed",
            result_ref=f"workspace://{workspace_dir}",
            message=error_summary[:2000],
            payload={
                "exit_code": exit_code,
                "tool_calls": tool_calls,
                "cost": cost_info,
                "final_text": final_text[:1000] if final_text else "",
            },
        )

    return MissionOutcome(
        status="completed",
        result_ref=f"workspace://{workspace_dir}",
        payload={
            "exit_code": exit_code,
            "tool_calls": tool_calls,
            "cost": cost_info,
            "final_text": final_text[:4000] if final_text else "",
            "stream_lines": len(stream_lines),
        },
    )


def _build_cli_env(enable_agent_teams: bool, workspace_dir: Path) -> dict[str, str]:
    """Build the environment for the CLI subprocess."""
    env = dict(os.environ)

    if enable_agent_teams:
        env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"

    env["CURRENT_RUN_WORKSPACE"] = str(workspace_dir)
    env["CURRENT_SESSION_WORKSPACE"] = str(workspace_dir)
    env.pop("UA_INFISICAL_STRICT", None)  # Don't enforce Infisical in CLI subprocess

    # Forward Agent Team concurrency limit if set
    max_agents = os.getenv("REPORT_MAX_CONCURRENT_AGENTS", "")
    if max_agents:
        env["REPORT_MAX_CONCURRENT_AGENTS"] = max_agents

    return env


def _build_cli_prompt(
    objective: str,
    payload: dict[str, Any],
    workspace_dir: Path,
    skill_name: str,
) -> str:
    """Craft a well-structured prompt for the Claude Code CLI session."""

    parts = []

    if skill_name:
        parts.append(
            f"Use the skill: {skill_name}\n"
            f"Invoke it with: Skill(skill='{skill_name}', args='{objective}')\n"
        )

    parts.append(f"## Objective\n\n{objective}\n")

    parts.append(f"## Workspace\n\nAll output files go under: {workspace_dir}\n")

    constraints = payload.get("constraints")
    if isinstance(constraints, dict) and constraints:
        parts.append("## Constraints\n")
        for k, v in constraints.items():
            parts.append(f"- {k}: {v}")
        parts.append("")

    corpus_path = str(payload.get("corpus_path") or "").strip()
    if corpus_path:
        parts.append(f"## Input Corpus\n\nResearch corpus is at: {corpus_path}\n")

    output_dir = str(payload.get("output_dir") or "").strip()
    if output_dir:
        parts.append(f"## Output Directory\n\nWrite final deliverables to: {output_dir}\n")

    parts.append(
        "## Instructions\n\n"
        "Work autonomously to complete the objective. "
        "When finished, provide a clear summary of what was produced and where the output files are located."
    )

    return "\n".join(parts)


def _build_retry_prompt(original_prompt: str, previous_error: str, attempt: int) -> str:
    """Build a retry prompt incorporating the previous failure."""
    return (
        f"## Retry Attempt {attempt}\n\n"
        f"The previous attempt failed with this error:\n"
        f"```\n{previous_error[:500]}\n```\n\n"
        f"Please address the issue and try again.\n\n"
        f"---\n\n{original_prompt}"
    )


def _resolve_workspace(
    mission_id: str,
    workspace_root: Path,
    payload: dict[str, Any],
) -> Path:
    """Resolve the workspace directory for the CLI session.

    When a target_path is provided, it is validated against the workspace
    guardrail to prevent writes into the UA repository tree.
    """
    target_path = str(payload.get("target_path") or "").strip()
    if target_path:
        resolved = Path(target_path).expanduser().resolve()
        _enforce_cli_target_guardrails(resolved, payload=payload)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    safe_id = mission_id.replace("/", "_").replace("..", "_").strip() or f"cli_{uuid.uuid4().hex[:8]}"
    return (workspace_root / safe_id).resolve()


def _enforce_cli_target_guardrails(target: Path, *, payload: dict[str, Any]) -> None:
    """Block CLI sessions from writing into the UA repository tree."""
    if not vp_hard_block_ua_repo(default=True):
        return
    if repo_mutation_requested(payload) and is_approved_codebase_path(target):
        return
    handoff = Path(vp_handoff_root()).expanduser().resolve()

    repo_root = Path(__file__).resolve().parents[4]
    blocked_roots = [
        repo_root.resolve(),
        (repo_root / "AGENT_RUN_WORKSPACES").resolve(),
        (repo_root / "artifacts").resolve(),
        (repo_root / "Memory_System").resolve(),
    ]
    try:
        enforce_external_target_path(
            target,
            blocked_roots=blocked_roots,
            allowlisted_roots=[handoff],
            operation="CLI target path",
        )
    except WorkspaceGuardError as exc:
        raise ValueError(
            "CLI target path is blocked inside UA repository/runtime roots. "
            f"Use handoff root {handoff} or another external path. ({exc})"
        ) from exc


def _parse_payload(payload_json: Any) -> dict[str, Any]:
    """Parse mission payload from JSON string or dict."""
    if isinstance(payload_json, dict):
        return payload_json
    if isinstance(payload_json, str) and payload_json.strip():
        try:
            loaded = json.loads(payload_json)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            return {}
    return {}


def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Gracefully terminate a subprocess (SIGTERM first, then SIGKILL)."""
    if proc.returncode is not None:
        return  # Already exited
    try:
        proc.send_signal(signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    except Exception as exc:
        logger.debug("SIGTERM failed for CLI process: %s", exc)

    # Give it 5 seconds to terminate gracefully, then force-kill
    async def _wait_and_kill():
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_wait_and_kill())
        else:
            # Fallback: immediate kill if no running loop
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
    except RuntimeError:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def _save_stream_log(
    workspace_dir: Path,
    stream_lines: list[str],
    stderr_text: str,
    exit_code: int,
) -> None:
    """Save the CLI output stream to a log file for debugging."""
    try:
        log_path = workspace_dir / "cli_stream.log"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"# Claude Code CLI Stream Log\n")
            f.write(f"# Exit code: {exit_code}\n")
            f.write(f"# Lines: {len(stream_lines)}\n\n")
            for line in stream_lines:
                f.write(f"{line}\n")
            if stderr_text:
                f.write(f"\n# STDERR:\n{stderr_text}\n")
    except Exception as exc:
        logger.debug("Failed to save CLI stream log: %s", exc)
