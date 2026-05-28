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
# StreamReader line buffer for the spawned claude CLI's stdout/stderr.
# Default asyncio limit is 64 KiB, but the Claude CLI's stream-json output
# legitimately emits single lines well above that (large tool_result blocks
# from file reads, web fetches, large assistant messages). Hitting the
# default raises asyncio.LimitOverrunError inside _monitor_cli_output and
# fails the entire mission with "Error monitoring CLI: ...". 10 MiB matches
# the headroom the Anthropic SDK uses for similar streams; a single line
# above that is itself a defect worth surfacing.
CLI_STREAM_BUFFER_LIMIT = 10 * 1024 * 1024

# Substrings that indicate the CLI rejected our credentials. Retrying with
# the same env is pointless — the OAuth access token has expired or the
# API key is invalid, and only an operator re-auth can fix it. Matching is
# case-insensitive. Keep these phrases tight to avoid false positives on
# unrelated 401s mentioned inside generated content.
_AUTH_FAILURE_MARKERS = (
    "invalid authentication credentials",
    "failed to authenticate",
    "invalid x-api-key",
    "authentication_error",
)

_AUTH_FAILURE_OPERATOR_HINT = (
    "Claude CLI authentication failed (401). The Anthropic OAuth access "
    "token on this host has likely expired and headless `claude --print` "
    "does not refresh it. Re-auth on the VPS as the runtime user: run "
    "`claude setup-token` (long-lived token, recommended for headless) or "
    "`claude` to drive the interactive browser OAuth flow."
)


def _is_auth_failure(outcome: "MissionOutcome") -> bool:
    """Return True if the CLI exit looks like an authentication failure.

    We inspect both the top-level ``message`` (e.g. stderr summary) and the
    ``payload.final_text`` (where the CLI's own error string lands when it
    exits cleanly with code 1 from an auth rejection). One match in either
    field is enough — auth failures are deterministic and not worth a
    fuzzy threshold.
    """
    haystack_parts: list[str] = []
    if outcome.message:
        haystack_parts.append(str(outcome.message))
    payload = outcome.payload or {}
    final_text = payload.get("final_text")
    if final_text:
        haystack_parts.append(str(final_text))
    haystack = " ".join(haystack_parts).lower()
    if not haystack:
        return False
    return any(marker in haystack for marker in _AUTH_FAILURE_MARKERS)


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

        # Hermes Phase E.2.a — resolve Cody execution mode from the
        # mission's metadata block. Falls back to env / "zai" default
        # if the mission row predates Phase E.1.
        from universal_agent.services.cody_mode import resolve_from_payload

        cody_mode = resolve_from_payload(
            payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
        )
        if cody_mode == "anthropic":
            logger.info(
                "CLI mission %s — Cody mode=anthropic; scrubbing ANTHROPIC_* env "
                "and forcing CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1",
                mission_id,
            )

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

                _cli_task_id = str(payload.get("task_id") or "").strip()
                outcome = await _execute_cli_session(
                    prompt=current_prompt,
                    workspace_dir=workspace_dir,
                    timeout_seconds=timeout_seconds,
                    enable_agent_teams=enable_agent_teams,
                    mission_id=mission_id,
                    cody_mode=cody_mode,
                    task_id=_cli_task_id,
                )

                # Hermes Phase F.1 / F.3 — classify the CLI exit and, if
                # this was a protocol violation (rc=0 but linked task
                # still in_progress), route the task into needs_review.
                # Also persists the classification onto the run row via
                # _close_run.  Best-effort: never blocks dispatch.
                _classify_and_route_cli_exit(
                    outcome=outcome,
                    task_id=_cli_task_id,
                    mission_id=mission_id,
                )

                if outcome.status == "completed":
                    # Hermes Phase E.2b — record token usage for the dashboard
                    # tile. Best-effort, never raises. Captures every
                    # completed mission regardless of cody_mode so the
                    # operator can compare costs across modes.
                    _record_mission_token_usage(
                        outcome=outcome,
                        mission_id=mission_id,
                        task_id=str(payload.get("task_id") or "").strip() or None,
                        cody_mode=cody_mode,
                    )
                    return outcome

                # Short-circuit retries on credential failure — the same
                # env will produce the same 401, and burning two more
                # retries delays the operator-visible signal.
                if _is_auth_failure(outcome):
                    logger.error(
                        "CLI mission %s aborted: authentication rejected by "
                        "Anthropic API (cody_mode=%s, attempt=%d). %s",
                        mission_id, cody_mode, attempt,
                        _AUTH_FAILURE_OPERATOR_HINT,
                    )
                    enriched_payload = dict(outcome.payload or {})
                    enriched_payload["auth_failure"] = True
                    enriched_payload["operator_hint"] = _AUTH_FAILURE_OPERATOR_HINT
                    return MissionOutcome(
                        status="failed",
                        result_ref=outcome.result_ref,
                        message=f"{_AUTH_FAILURE_OPERATOR_HINT} "
                                f"(original CLI error: {outcome.message})",
                        payload=enriched_payload,
                    )

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
    cody_mode: str = "zai",
    task_id: str = "",
) -> MissionOutcome:
    """Spawn a claude CLI subprocess and monitor its output.

    Hermes Phase F site-wiring — if ``task_id`` is provided, the
    spawned subprocess's PID is recorded on the linked Task Hub
    assignment (best-effort) and the eventual exit is fed into
    ``classify_worker_exit`` upstream in :func:`run_mission`. Empty
    ``task_id`` skips F.1 bookkeeping silently.
    """

    env = _build_cli_env(enable_agent_teams, workspace_dir, cody_mode=cody_mode)

    cmd = [
        "claude",
        "--print",
        "--output-format", "stream-json",
        "--verbose",
    ]

    # Model selection — the Claude Code CLI defaults to Sonnet
    # (claude-sonnet-4-6). For Cody's autonomous coding work we default
    # to Opus 4.7 (Anthropic positions it as the most capable model for
    # complex agentic coding — a step-change over Sonnet 4.6 on the
    # 93-task coding benchmark). The operator can override per-process
    # by setting ``UA_CODY_CLI_MODEL``:
    #   - explicit value (e.g. ``claude-sonnet-4-6``) → pinned to that
    #   - ``default`` or empty → use the CLI's default (no ``--model``
    #     flag, currently Sonnet)
    # Only applied when ``cody_mode == "anthropic"`` — ZAI/SDK paths
    # have their own model routing and would ignore ``--model`` anyway.
    if cody_mode == "anthropic":
        model_override = os.getenv("UA_CODY_CLI_MODEL", "claude-opus-4-7").strip()
        if model_override and model_override.lower() != "default":
            cmd.extend(["--model", model_override])

    logger.info("Launching CLI: %s (cwd=%s, timeout=%ds)", " ".join(cmd), workspace_dir, timeout_seconds)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace_dir),
            env=env,
            start_new_session=True,
            limit=CLI_STREAM_BUFFER_LIMIT,
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

    # Hermes Phase F.1 site-wiring — if the mission carries a linked
    # Task Hub task_id, stamp the spawned subprocess PID onto its
    # active assignment row.  Best-effort; never blocks the happy
    # path of the CLI session.
    cli_assignment_id: Optional[str] = None
    if task_id and proc.pid:
        try:
            from universal_agent import task_hub as _f_th
            from universal_agent.gateway_server import (
                _task_hub_open_conn as _f_open_conn,
            )
            from universal_agent.services.worker_exit_classifier import (
                find_active_assignment_for_task as _f_find_aid,
            )
            _f_conn = _f_open_conn()
            try:
                cli_assignment_id = _f_find_aid(_f_conn, task_id=task_id)
                if cli_assignment_id:
                    _f_th.record_worker_pid(
                        _f_conn,
                        assignment_id=cli_assignment_id,
                        worker_pid=int(proc.pid),
                    )
                    _f_conn.commit()
            finally:
                _f_conn.close()
        except Exception as _f_exc:
            logger.debug(
                "Phase F.1 record_worker_pid skipped for CLI mission %s: %s",
                mission_id, _f_exc,
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
        prompt=prompt,
    )

    # Phase F.1 — enrich the outcome payload with PID + assignment +
    # timeout-kill signal so run_mission can classify the exit.
    enriched_payload = dict(result.payload or {})
    if proc.pid:
        enriched_payload.setdefault("worker_pid", int(proc.pid))
    if cli_assignment_id:
        enriched_payload.setdefault("assignment_id", cli_assignment_id)
    # PR #492 — expose the spawn cwd so worker_loop's COMPLETION
    # attestation check can also look here when the canonical
    # mission_workspace doesn't have COMPLETION.md (BRIEF redirected
    # the agent to a non-canonical path like /tmp/foo).
    enriched_payload.setdefault("cli_workspace_dir", str(workspace_dir))
    # Heuristic: a "timed out" message indicates _monitor_cli_output\u2019s
    # timeout path fired (it calls ``_kill_process`` and returns
    # status="failed" with a "timed out" message).
    if (
        result.status == "failed"
        and result.message
        and "timed out" in result.message.lower()
    ):
        enriched_payload.setdefault("was_timeout_killed", True)

    # Accumulate Cody identifiers (CLI session_id, mission_id, workspace
    # path, worker PID) on the parent Task Hub row's
    # ``metadata.dispatch.cody_*`` so the dashboard card can render a
    # progressive Delegation Trace and the Workspace button can deep-link
    # into the Cody CLI session.
    #
    # We write to the PARENT task row (e.g. ``qa-af037bdaa324``) — NOT to
    # the orchestrator's (Simone's) assignment row. PR #488 mistakenly
    # wrote to the assignment's ``provider_session_id``, but that field
    # belongs to whoever claimed the task (Simone, in the operator →
    # Simone → Cody handoff). Overwriting it would lose Simone's session
    # identity, and in practice the write silently no-op'd because the
    # ``task_id`` propagation gap meant ``cli_assignment_id`` was None.
    #
    # Best-effort — never blocks the happy path.
    _captured_sid = str(enriched_payload.get("cli_session_id") or "").strip()
    if task_id and (_captured_sid or proc.pid or mission_id or workspace_dir):
        try:
            from universal_agent import task_hub as _f_th
            from universal_agent.gateway_server import (
                _task_hub_open_conn as _f_open_conn,
            )
            _f_conn = _f_open_conn()
            try:
                _f_th.record_cody_dispatch_metadata(
                    _f_conn,
                    task_id=task_id,
                    cody_session_id=_captured_sid,
                    cody_mission_id=mission_id,
                    cody_workspace_dir=str(workspace_dir),
                    cody_worker_pid=int(proc.pid) if proc.pid else 0,
                )
                _f_conn.commit()
            finally:
                _f_conn.close()
        except Exception as _f_exc:
            logger.debug(
                "record_cody_dispatch_metadata skipped for CLI mission %s: %s",
                mission_id, _f_exc,
            )

    return MissionOutcome(
        status=result.status,
        result_ref=result.result_ref,
        message=result.message,
        payload=enriched_payload,
    )


async def _monitor_cli_output(
    *,
    proc: asyncio.subprocess.Process,
    timeout_seconds: int,
    workspace_dir: Path,
    mission_id: str,
    prompt: str = "",
) -> MissionOutcome:
    """Read the CLI's JSON stream output and extract the result.

    Emits a ``run.log`` in ``workspace_dir`` alongside the raw
    ``cli_stream.log``. ``run.log`` matches the format the gateway's
    chat handler writes (`[HH:MM:SS] 👤 USER: …`, `🤖 ASSISTANT: …`,
    `🔧 TOOL CALL: …`, `📦 TOOL RESULT (N bytes)`, `ERROR: …`, and a
    closing ``=== Turn completed (N tool calls) ===`` line). This is
    the canonical durable rehydration source for the three-panel
    viewer — without it, the viewer can't render Cody's session even
    after the resolver maps the cody_session_id to this workspace.
    """

    final_text = ""
    cost_info: dict[str, Any] = {}
    tool_calls = 0
    errors: list[str] = []
    last_output_time = time.monotonic()
    stream_lines: list[str] = []
    cli_session_id: str = ""

    # Open run.log handle for live rehydration source. The viewer's
    # rehydration path reads this file (see app/page.tsx). We write
    # incrementally so an operator opening the workspace while Cody
    # is mid-run sees in-flight progress.
    run_log_handle = None
    try:
        from datetime import datetime, timezone
        workspace_dir.mkdir(parents=True, exist_ok=True)
        run_log_path = workspace_dir / "run.log"
        run_log_handle = open(run_log_path, "a", encoding="utf-8")
        if prompt:
            ts0 = datetime.now(timezone.utc).strftime("%H:%M:%S")
            run_log_handle.write(f"[{ts0}] 👤 USER: {prompt}\n")
            run_log_handle.flush()
    except Exception:
        run_log_handle = None

    def _rl_write(line: str) -> None:
        if run_log_handle is None:
            return
        try:
            run_log_handle.write(line + "\n")
            run_log_handle.flush()
        except Exception:
            pass

    def _rl_ts() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%H:%M:%S")

    def _rl_close(reason: str) -> None:
        nonlocal run_log_handle
        if run_log_handle is None:
            return
        try:
            run_log_handle.write(
                f"[{_rl_ts()}] === {reason} ({tool_calls} tool calls) ===\n"
            )
            run_log_handle.close()
        except Exception:
            pass
        run_log_handle = None

    try:
        deadline = time.monotonic() + timeout_seconds

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("CLI mission %s timed out after %ds", mission_id, timeout_seconds)
                _kill_process(proc)
                _rl_close("Turn timed out")
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

            # Capture the CLI subprocess's session_id on the first event
            # that emits it. Every stream-json event from `claude --print`
            # carries a `session_id` field; we keep the first non-empty
            # value seen so the Task Hub card's Workspace button can
            # deep-link to this CLI session instead of the orchestrator
            # (e.g. Simone) session that dispatched the mission.
            if not cli_session_id:
                _sid = str(event.get("session_id") or "").strip()
                if _sid:
                    cli_session_id = _sid

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
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type == "text":
                            text = str(block.get("text") or "")
                            final_text = text
                            stripped = text.rstrip()
                            if stripped:
                                _rl_write(f"[{_rl_ts()}] 🤖 ASSISTANT: {stripped}")
                        elif block_type == "tool_use":
                            tool_name = str(block.get("name") or "unknown")
                            _rl_write(f"[{_rl_ts()}] 🔧 TOOL CALL: {tool_name}")
                        elif block_type == "tool_result":
                            tc = block.get("content")
                            tc_size = len(str(tc)) if tc is not None else 0
                            _rl_write(f"[{_rl_ts()}] 📦 TOOL RESULT ({tc_size} bytes)")

            elif event_type == "tool_use":
                tool_calls += 1
                tool_name = str(event.get("name") or "unknown")
                _rl_write(f"[{_rl_ts()}] 🔧 TOOL CALL: {tool_name}")

            elif event_type == "tool_result":
                tc = event.get("content")
                tc_size = len(str(tc)) if tc is not None else 0
                _rl_write(f"[{_rl_ts()}] 📦 TOOL RESULT ({tc_size} bytes)")

            elif event_type == "error":
                error_msg = str(event.get("error") or event.get("message") or "unknown error")
                errors.append(error_msg)
                logger.warning("CLI mission %s error event: %s", mission_id, error_msg)
                _rl_write(f"[{_rl_ts()}] ERROR: {error_msg}")

    except Exception as exc:
        logger.exception("Error monitoring CLI output for mission %s", mission_id)
        _kill_process(proc)
        _rl_close(f"Monitor crashed: {exc}")
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
        failed_payload: dict[str, Any] = {
            "exit_code": exit_code,
            "tool_calls": tool_calls,
            "cost": cost_info,
            "final_text": final_text[:1000] if final_text else "",
        }
        if cli_session_id:
            failed_payload["cli_session_id"] = cli_session_id
        _rl_close(f"Turn failed (exit={exit_code})")
        return MissionOutcome(
            status="failed",
            result_ref=f"workspace://{workspace_dir}",
            message=error_summary[:2000],
            payload=failed_payload,
        )

    completed_payload: dict[str, Any] = {
        "exit_code": exit_code,
        "tool_calls": tool_calls,
        "cost": cost_info,
        "final_text": final_text[:4000] if final_text else "",
        "stream_lines": len(stream_lines),
    }
    if cli_session_id:
        completed_payload["cli_session_id"] = cli_session_id
    _rl_close("Turn completed")
    return MissionOutcome(
        status="completed",
        result_ref=f"workspace://{workspace_dir}",
        payload=completed_payload,
    )


def _classify_and_route_cli_exit(
    *,
    outcome: MissionOutcome,
    task_id: str,
    mission_id: str,
) -> None:
    """Hermes Phase F.1 / F.3 — classify a CLI mission exit and route.

    Called from :meth:`ClaudeCodeCLIClient.run_mission` immediately
    after ``_execute_cli_session`` returns.  Pulls ``exit_code`` +
    ``was_timeout_killed`` + ``assignment_id`` out of
    ``outcome.payload`` (populated in ``_execute_cli_session``\u2019s
    enriched return path) and feeds them into
    :func:`classify_worker_exit`.

    Side effects (all best-effort, never raise):
      * Logs the classified outcome with mission_id + task_id +
        assignment_id for grep-ability.
      * If a linked assignment exists, writes the classification onto
        the open run row via ``task_hub._close_run``.
      * If the classification is a protocol violation
        (``clean_exit_zero_no_disposition`` — rc=0 but task is still
        in_progress), parks the task in ``needs_review`` with the
        canonical ``protocol_violation_vp_cli_clean_exit_no_disposition``
        reason so Phase B.1\u2019s ``rehydrate`` / ``re_evaluate`` verbs
        can act on it.

    No-ops silently if ``task_id`` is empty (the common case — most VP
    missions don\u2019t carry a direct Task Hub linkage).
    """
    tid = str(task_id or "").strip()
    if not tid:
        # No linked task — just log the classification at debug for
        # observability and return.  We still classify so the log
        # carries the outcome bucket.
        try:
            from universal_agent.services.worker_exit_classifier import (
                classify_worker_exit as _f_classify,
            )
            _exit_code = None
            _was_timeout_killed = False
            if outcome.payload:
                _exit_code = outcome.payload.get("exit_code")
                _was_timeout_killed = bool(outcome.payload.get("was_timeout_killed"))
            _was_signaled = bool(
                isinstance(_exit_code, int)
                and _exit_code < 0
                and not _was_timeout_killed
            )
            _classification = _f_classify(
                return_code=_exit_code if isinstance(_exit_code, int) else None,
                was_signaled=_was_signaled,
                was_timeout_killed=_was_timeout_killed,
                # No linked task — treat outcome.status as ground truth.
                task_closed_normally=outcome.status == "completed",
            )
            logger.debug(
                "Phase F.1 CLI mission %s exit classified as %s (no linked task)",
                mission_id, _classification.outcome,
            )
        except Exception as _f_exc:
            logger.debug(
                "Phase F.1 classification skipped for mission %s: %s",
                mission_id, _f_exc,
            )
        return

    try:
        from universal_agent import task_hub as _f_th
        from universal_agent.gateway_server import (
            _task_hub_open_conn as _f_open_conn,
        )
        from universal_agent.services.worker_exit_classifier import (
            classify_worker_exit as _f_classify,
            park_task_for_protocol_violation as _f_park,
            task_was_closed_normally as _f_closed,
        )

        _exit_code = None
        _was_timeout_killed = False
        _assignment_id = ""
        if outcome.payload:
            _exit_code = outcome.payload.get("exit_code")
            _was_timeout_killed = bool(outcome.payload.get("was_timeout_killed"))
            _assignment_id = str(outcome.payload.get("assignment_id") or "")

        _was_signaled = bool(
            isinstance(_exit_code, int)
            and _exit_code < 0
            and not _was_timeout_killed
        )

        _conn = _f_open_conn()
        try:
            _closed_normally = _f_closed(_conn, task_id=tid)
            _classification = _f_classify(
                return_code=_exit_code if isinstance(_exit_code, int) else None,
                was_signaled=_was_signaled,
                was_timeout_killed=_was_timeout_killed,
                task_closed_normally=_closed_normally,
            )
            logger.info(
                "Phase F.1 CLI mission %s exit classified as %s "
                "(task=%s, assignment=%s, rc=%s)",
                mission_id, _classification.outcome, tid,
                _assignment_id or "<none>", _exit_code,
            )
            if _assignment_id:
                try:
                    _f_th._close_run(
                        _conn,
                        assignment_id=_assignment_id,
                        outcome=(
                            "completed"
                            if outcome.status == "completed"
                            else "failed"
                        ),
                        summary=(outcome.message or "")[:200],
                        error=(
                            ""
                            if outcome.status == "completed"
                            else (outcome.message or "")[:500]
                        ),
                        metadata={
                            "worker_exit": _classification.to_dict(),
                            "site": "vp_cli",
                            "mission_id": mission_id,
                        },
                    )
                    _conn.commit()
                except Exception as _close_exc:
                    logger.debug(
                        "Phase F.1 _close_run skipped for CLI mission %s: %s",
                        mission_id, _close_exc,
                    )
            if _classification.is_protocol_violation:
                _f_park(
                    _conn,
                    task_id=tid,
                    site="vp_cli",
                    summary=f"vp cli mission_id={mission_id}",
                    agent_id="vp_cli_client",
                )
        finally:
            _conn.close()
    except Exception as exc:
        logger.debug(
            "Phase F.1/F.3 wiring skipped for CLI mission %s: %s",
            mission_id, exc,
        )


def _record_mission_token_usage(
    *,
    outcome: MissionOutcome,
    mission_id: str,
    task_id: Optional[str],
    cody_mode: str,
) -> None:
    """Hermes Phase E.2b — persist token usage for the dashboard tile.

    Best-effort: never raises, never blocks the happy path. The CLI's
    ``result`` event populated ``outcome.payload["cost"]`` with the
    usage breakdown (``input_tokens`` etc.) and the model identifier
    sometimes lives in the same dict. Forwards everything we have to
    ``cody_token_tracking.record_token_usage``.
    """
    try:
        cost_info = (outcome.payload or {}).get("cost") if outcome.payload else None
        if not isinstance(cost_info, dict):
            cost_info = {}
        # Some Claude CLI stream-json variants put the model on the
        # result event directly; some nest it under usage. Best-effort.
        model = cost_info.get("model") or cost_info.get("model_id") or None

        from universal_agent.durable.db import (
            connect_runtime_db,
            get_activity_db_path,
        )
        from universal_agent.services.cody_token_tracking import (
            record_token_usage,
        )

        conn = connect_runtime_db(get_activity_db_path())
        try:
            record_token_usage(
                conn,
                cody_mode=cody_mode,
                mission_id=mission_id or None,
                task_id=task_id,
                model=str(model) if model else None,
                cost_info=cost_info,
            )
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "Token usage capture failed for mission %s (cody_mode=%s): %s",
            mission_id, cody_mode, exc,
            exc_info=False,
        )


def _build_cli_env(
    enable_agent_teams: bool,
    workspace_dir: Path,
    *,
    cody_mode: str = "zai",
) -> dict[str, str]:
    """Build the environment for the CLI subprocess.

    Hermes Phase E.2.a — when ``cody_mode == "anthropic"``, every
    ``ANTHROPIC_*`` env var is scrubbed so the spawned ``claude``
    subprocess falls through to its workspace-local OAuth (the user's
    Anthropic Max plan) instead of the UA daemon's ZAI routing. This
    mirrors the pattern in ``services/cody_implementation._scrubbed_env``
    that demo workspaces already use unconditionally.

    Defaulted to "zai" (current behavior — inherit parent ZAI routing).
    """
    if cody_mode == "anthropic":
        env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
        # Agent Teams is the whole point of Anthropic mode — force on.
        env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
        # Forward the long-lived Anthropic Max OAuth token (from
        # ``claude setup-token``, stored in Infisical as
        # ``CLAUDE_CODE_OAUTH_TOKEN``) into the subprocess env.
        #
        # Empirically verified 2026-05-26: ``claude setup-token`` produces a
        # token of the form ``sk-ant-oat01-...`` and explicitly tells the
        # operator: "Use this token by setting: export
        # CLAUDE_CODE_OAUTH_TOKEN=<token>". An earlier version of this code
        # translated the token into ``ANTHROPIC_API_KEY`` instead, which made
        # Claude Code reject it as "Invalid API key · Fix external API key"
        # because the OAuth token isn't valid in the API-key auth slot.
        #
        # CLAUDE_CODE_OAUTH_TOKEN does NOT start with ``ANTHROPIC_``, so it
        # would naturally pass through the dict comprehension above —
        # we read it explicitly here to make the contract obvious and
        # to support the legacy ``ANTHROPIC_MAX_OAUTH_TOKEN`` name as a
        # fallback during the rollout transition.
        oauth_token = (
            os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
            or os.environ.get("ANTHROPIC_MAX_OAUTH_TOKEN", "").strip()
        )
        if oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
            # Make absolutely sure no stale ANTHROPIC_API_KEY survives —
            # Claude Code prefers it over OAuth when both are present.
            env.pop("ANTHROPIC_API_KEY", None)
    else:
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
    """Craft a well-structured prompt for the Claude Code CLI session.

    When ``UA_VP_GOAL_ENABLED`` is set (and self-briefing artifacts already
    exist in the workspace from a prior briefing turn), this function still
    builds the work-phase prompt — but the briefing turn produced via
    ``services/self_briefing.build_self_briefing_prompt`` is what runs first.
    See ``worker_loop.py:_execute_mission_logic`` for the two-phase wiring.
    """

    parts = []

    goal_enabled = os.environ.get("UA_VP_GOAL_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

    if skill_name:
        parts.append(
            f"Use the skill: {skill_name}\n"
            f"Invoke it with: Skill(skill='{skill_name}', args='{objective}')\n"
        )

    parts.append(f"## Objective\n\n{objective}\n")

    parts.append(f"## Workspace\n\nAll output files go under: {workspace_dir}\n")

    # Self-briefing directive: when UA_VP_GOAL_ENABLED is on, prepend an
    # instruction telling the VP to invoke the self-brief-and-attest skill
    # FIRST. The skill drives the VP to write BRIEF.md (and ACCEPTANCE.md +
    # goal_condition.txt when /goal-eligible) before starting the work.
    # See .claude/skills/self-brief-and-attest/SKILL.md for the contract.
    if goal_enabled:
        brief_path = workspace_dir / "BRIEF.md"
        if not brief_path.exists():
            # No prior briefing turn — this run does briefing + work in one pass.
            parts.append(
                "## Self-briefing (REQUIRED FIRST STEP)\n\n"
                "Before any other work, invoke the `self-brief-and-attest` skill.\n"
                "Complete its Phase 1 (read + interrogate context), Phase 2 (write\n"
                "`BRIEF.md` at the workspace root). Then continue to the work below.\n"
                "If this mission is /goal-eligible (Cody + eligible source_kind),\n"
                "also complete Phase 3 (write `ACCEPTANCE.md` + `goal_condition.txt`).\n"
            )
        else:
            # Briefing artifacts already exist from a prior briefing turn — just
            # point the VP at them.
            acceptance_path = workspace_dir / "ACCEPTANCE.md"
            parts.append(
                f"## Self-briefing artifacts (from prior turn)\n\n"
                f"You have already self-briefed. Read these before starting work:\n"
                f"- `{brief_path}` — your interpretation of the task\n"
                + (f"- `{acceptance_path}` — verifiable success criteria\n" if acceptance_path.exists() else "")
            )

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

    # Completion-attestation reminder when UA_VP_GOAL_ENABLED is on.
    # The worker-level guard at worker_loop.py enforces this; the prompt
    # text here makes it explicit in the VP's context so the VP knows to
    # write the file before declaring done.
    if goal_enabled:
        parts.append(
            "## Completion attestation (REQUIRED)\n\n"
            "Before declaring this mission complete, write a `COMPLETION.md` file\n"
            "at the workspace root. See the `self-brief-and-attest` skill for the\n"
            "required structure. The parent worker enforces this — missing\n"
            "`COMPLETION.md` will route the mission into the failure-rescue lane\n"
            "as `failure_mode=\"missing_completion_attestation\"` and Simone\n"
            "will be notified.\n"
        )

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
