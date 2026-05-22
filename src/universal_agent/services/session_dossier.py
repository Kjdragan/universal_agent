"""Session Dossier Generator.

Generates comprehensive ``context_brief.md`` and ``description.txt`` files for
completed agent sessions.  Uses the Haiku model (GLM-4.5-Air via Z.AI
emulation) for cost-effective analysis of session artifacts.

The dossier serves three purposes:
  1. Session card description in the dashboard
  2. Context injection for "simulated rehydration" (context handoff)
  3. Daily memory digest aggregation
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Optional

from universal_agent.utils.model_resolution import resolve_haiku

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Async Anthropic Client (mirrors wiki/llm.py but non-blocking)
# ---------------------------------------------------------------------------

_SAFETY_MAX_INPUT_CHARS = 480_000  # ~120K tokens at ~4 chars/token

# Serialise dossier LLM calls across every caller in this process.
# Two paths race on session close — the hooks_service completion path
# (hourly at :40) and the gateway session reaper (half-hourly at :10/:40).
# Their overlap produced 100% of observed ZAI 429s in the P7 window
# (2026-05-21 → 22); a single-slot semaphore forces them to queue at
# the LLM boundary the way every other prolific caller already does.
_DOSSIER_SEMAPHORE = asyncio.Semaphore(1)


def _get_async_client():
    """Create an async Anthropic client using the ZAI emulation layer."""
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package not installed") from exc

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("No Anthropic API key available for dossier generation")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    return AsyncAnthropic(**client_kwargs)


async def _async_call_llm(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 2048,
) -> str:
    """Make an async LLM call and return the raw text response."""
    client = _get_async_client()

    response = await client.messages.create(
        model=model or resolve_haiku(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            raw_text += block.text

    return raw_text.strip()


# ---------------------------------------------------------------------------
# Dossier Generation Prompt
# ---------------------------------------------------------------------------

_DOSSIER_SYSTEM_PROMPT = """\
You are a session analyst for an autonomous AI agent platform. Given the \
execution data for an agent session, produce TWO outputs separated by \
"===DESCRIPTION===":

1. A comprehensive Session Dossier in markdown covering:
   - **Original Task**: What the user or system requested
   - **Execution Summary**: Duration, number of tool calls/iterations, final status
   - **Key Actions Taken**: Numbered list of the main things the agent did
   - **Decisions & Reasoning**: Why certain approaches were chosen, pivots made
   - **Artifacts Produced**: Files created or modified (with full paths when available)
   - **Workspace Location**: The full path to the session workspace
   - **Conversation Highlights**: Key exchanges that preserve reasoning, discoveries, or pivots
   - **Errors / Blockers**: Any failures, retries, or issues encountered
   - **State at Close**: What was left incomplete, what needs follow-up

   Be thorough — this dossier will be used to reconstruct context for future \
   agent sessions that need to continue this work. Include specific details, \
   file paths, decisions, and reasoning. Do NOT summarize away important details.

2. After the separator "===DESCRIPTION===", write a 1-2 sentence description \
   suitable for a dashboard card. This should read like a title/subtitle — \
   concise but informative. Do NOT include the session ID or timestamps.

Important: The dossier should be 1-2 pages of markdown. The description should \
be 1-2 sentences maximum."""

_DESCRIPTION_SEPARATOR = "===DESCRIPTION==="


# ---------------------------------------------------------------------------
# Content-filter (pure Python, no LLM)
# ---------------------------------------------------------------------------
# Skip the LLM call when the workspace has no meaningful execution data.
# Catches the common prod case where cron-spawned workspaces close with an
# empty run.log (the real work happened in a !script subprocess that didn't
# route output through run.log). Content-based, NOT workspace-name-based —
# any session type that DOES start producing real logs gets a dossier with
# no code changes needed.

_DEFAULT_MIN_RUN_LOG_BYTES = 500
_DEFAULT_MIN_TRANSCRIPT_BYTES = 500
_CHECKPOINT_REQUEST_KEYS = ("original_request", "query", "task")


def _min_run_log_bytes() -> int:
    try:
        return max(0, int(os.getenv("UA_DOSSIER_MIN_RUN_LOG_BYTES",
                                    str(_DEFAULT_MIN_RUN_LOG_BYTES))))
    except ValueError:
        return _DEFAULT_MIN_RUN_LOG_BYTES


def _min_transcript_bytes() -> int:
    try:
        return max(0, int(os.getenv("UA_DOSSIER_MIN_TRANSCRIPT_BYTES",
                                    str(_DEFAULT_MIN_TRANSCRIPT_BYTES))))
    except ValueError:
        return _DEFAULT_MIN_TRANSCRIPT_BYTES


def _has_meaningful_content(workspace: Path) -> tuple[bool, dict]:
    """Return (True, reason_dict) if the workspace has anything worth
    summarizing. Pure-Python heuristic — no LLM call, microseconds to run.

    Returns True on the FIRST signal that matches:
      - run.log file size > UA_DOSSIER_MIN_RUN_LOG_BYTES (default 500)
      - transcript.md file size > UA_DOSSIER_MIN_TRANSCRIPT_BYTES (default 500)
      - run_checkpoint.json or session_checkpoint.json parses AND has a
        non-empty value for original_request / query / task
      - sync_ready.json parses AND tool_calls > 0

    Returns False if ALL of the above are absent — meaning the workspace
    closed with nothing real to summarize.
    """
    workspace = Path(workspace)

    # 1. run.log byte size
    run_log = workspace / "run.log"
    try:
        if run_log.is_file():
            size = run_log.stat().st_size
            if size >= _min_run_log_bytes():
                return True, {"matched": "run.log", "bytes": size}
    except OSError:
        pass

    # 2. transcript.md byte size
    transcript = workspace / "transcript.md"
    try:
        if transcript.is_file():
            size = transcript.stat().st_size
            if size >= _min_transcript_bytes():
                return True, {"matched": "transcript.md", "bytes": size}
    except OSError:
        pass

    # 3. checkpoint with original request / query / task
    for cp_name in ("run_checkpoint.json", "session_checkpoint.json"):
        cp_path = workspace / cp_name
        if not cp_path.is_file():
            continue
        try:
            cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(cp_data, dict):
            continue
        for key in _CHECKPOINT_REQUEST_KEYS:
            val = cp_data.get(key)
            if isinstance(val, str) and val.strip():
                return True, {"matched": f"{cp_name}:{key}"}

    # 4. sync_ready.json with non-zero tool_calls
    sync_ready = workspace / "sync_ready.json"
    if sync_ready.is_file():
        try:
            sr_data = json.loads(sync_ready.read_text(encoding="utf-8"))
            if isinstance(sr_data, dict):
                tool_calls = sr_data.get("tool_calls")
                if isinstance(tool_calls, (int, float)) and tool_calls > 0:
                    return True, {"matched": "sync_ready.tool_calls",
                                  "tool_calls": int(tool_calls)}
        except (json.JSONDecodeError, OSError):
            pass

    return False, {"matched": None}


# ---------------------------------------------------------------------------
# Workspace Data Collection
# ---------------------------------------------------------------------------


def _collect_workspace_data(workspace: Path, metadata: dict) -> str:
    """Assemble the user prompt content from workspace artifacts."""
    parts: list[str] = []

    # --- Metadata ---
    if metadata:
        parts.append("## Session Metadata")
        for k, v in metadata.items():
            parts.append(f"- **{k}**: {v}")
        parts.append("")

    # --- Workspace path ---
    parts.append(f"## Workspace\n{workspace}\n")

    # --- Run log (primary source) ---
    run_log_path = workspace / "run.log"
    if run_log_path.exists():
        try:
            run_log_text = run_log_path.read_text(encoding="utf-8", errors="ignore")
            if len(run_log_text) > _SAFETY_MAX_INPUT_CHARS:
                run_log_text = run_log_text[:_SAFETY_MAX_INPUT_CHARS] + "\n\n[... truncated at safety limit ...]"
            parts.append("## Run Log\n```\n" + run_log_text + "\n```\n")
        except Exception as exc:
            parts.append(f"## Run Log\n[Error reading run.log: {exc}]\n")

    # --- Checkpoint (original request) ---
    for checkpoint_name in ("run_checkpoint.json", "session_checkpoint.json"):
        cp_path = workspace / checkpoint_name
        if cp_path.exists():
            try:
                payload = json.loads(cp_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    original_request = payload.get("original_request") or payload.get("query") or payload.get("task")
                    if original_request:
                        parts.append(f"## Original Request (from {checkpoint_name})\n{original_request}\n")
                    break
            except Exception:
                pass

    # --- Sync ready marker (execution stats) ---
    sync_ready_path = workspace / "sync_ready.json"
    if sync_ready_path.exists():
        try:
            sr = json.loads(sync_ready_path.read_text(encoding="utf-8"))
            if isinstance(sr, dict):
                parts.append("## Execution Stats (sync_ready.json)")
                for k in ("status", "duration_seconds", "tool_calls", "iterations", "errors", "terminal_reason"):
                    if k in sr:
                        parts.append(f"- **{k}**: {sr[k]}")
                parts.append("")
        except Exception:
            pass

    # --- File listing ---
    try:
        file_entries = []
        for item in sorted(workspace.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                sub_count = sum(1 for _ in item.rglob("*") if _.is_file())
                file_entries.append(f"  📁 {item.name}/ ({sub_count} files)")
            elif item.is_file():
                size_kb = item.stat().st_size / 1024
                file_entries.append(f"  📄 {item.name} ({size_kb:.1f} KB)")
        if file_entries:
            parts.append("## Workspace Files\n" + "\n".join(file_entries) + "\n")
    except Exception as exc:
        parts.append(f"## Workspace Files\n[Error listing files: {exc}]\n")

    # --- Transcript excerpt (if run.log was missing) ---
    if not run_log_path.exists():
        transcript_path = workspace / "transcript.md"
        if transcript_path.exists():
            try:
                transcript_text = transcript_path.read_text(encoding="utf-8", errors="ignore")
                if len(transcript_text) > _SAFETY_MAX_INPUT_CHARS:
                    transcript_text = transcript_text[:_SAFETY_MAX_INPUT_CHARS] + "\n\n[... truncated ...]"
                parts.append("## Transcript\n" + transcript_text + "\n")
            except Exception:
                pass

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_session_dossier(
    workspace: Path,
    metadata: Optional[dict] = None,
) -> tuple[str, str]:
    """Generate a context_brief.md and description.txt for a session.

    Args:
        workspace: Path to the session workspace directory.
        metadata: Optional dict of session metadata (source, hook_name, etc.).

    Returns:
        Tuple of (dossier_text, description_text).

    Raises:
        RuntimeError: If the LLM call fails or no API key is available.

    """
    if metadata is None:
        metadata = {}

    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(f"Workspace directory not found: {workspace}")

    # Content-based skip (2026-05-22): pure-Python check that bypasses the
    # LLM when there's nothing meaningful to summarize. Catches the common
    # prod case where cron-spawned workspaces close with an empty run.log
    # (the real work happened in a !script subprocess that didn't route
    # output through run.log). Saves ~75-85% of dossier LLM calls.
    # CONTENT-based, NOT workspace-name-based — any session type that
    # starts producing real logs gets a dossier with no code change.
    has_content, _reasons = _has_meaningful_content(workspace)
    if not has_content:
        logger.info(
            "Skipping dossier for %s: no meaningful execution data "
            "(empty/missing run.log, transcript, checkpoint, sync_ready). %s",
            workspace.name,
            _reasons,
        )
        dossier = (
            "# Session Dossier\n\n"
            "No meaningful execution data found to summarize. The workspace "
            "had no run.log content, no transcript, no original-request "
            "checkpoint, and no non-zero tool-call record. Skipped LLM "
            "summarization to conserve inference budget.\n"
        )
        description = "Empty session — no meaningful execution data"
        _write_files(workspace, dossier, description)
        return dossier, description

    user_content = _collect_workspace_data(workspace, metadata)

    logger.info(
        "Generating dossier for %s (%d chars input)",
        workspace.name,
        len(user_content),
    )

    async with _DOSSIER_SEMAPHORE:
        raw_response = await _async_call_llm(
            system=_DOSSIER_SYSTEM_PROMPT,
            user=user_content,
            model=resolve_haiku(),
            max_tokens=2048,
        )

    # Parse the response
    if _DESCRIPTION_SEPARATOR in raw_response:
        parts = raw_response.split(_DESCRIPTION_SEPARATOR, 1)
        dossier = parts[0].strip()
        description = parts[1].strip()
    else:
        # Fallback: use entire response as dossier, extract first sentence as description
        dossier = raw_response.strip()
        first_line = dossier.split("\n")[0].strip().lstrip("#").strip()
        description = first_line[:200] if first_line else "Session completed"

    # Normalize description — single line, no markdown
    description = " ".join(description.split()).strip()
    if len(description) > 300:
        description = description[:297].rstrip() + "…"

    _write_files(workspace, dossier, description)

    logger.info(
        "Dossier generated for %s (dossier=%d chars, description=%d chars)",
        workspace.name,
        len(dossier),
        len(description),
    )

    return dossier, description


def _write_files(workspace: Path, dossier: str, description: str) -> None:
    """Write context_brief.md and description.txt to the workspace."""
    try:
        (workspace / "context_brief.md").write_text(dossier, encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write context_brief.md to %s: %s", workspace, exc)

    try:
        (workspace / "description.txt").write_text(description, encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write description.txt to %s: %s", workspace, exc)


async def backfill_missing_dossiers(
    workspaces_dir: Path,
    max_sessions: int = 50,
) -> int:
    """Scan workspace directories and generate dossiers for those missing one.

    Args:
        workspaces_dir: Root directory containing session workspace subdirectories.
        max_sessions: Maximum number of sessions to process in one run.

    Returns:
        Count of sessions that were successfully backfilled.

    """
    workspaces_dir = Path(workspaces_dir).resolve()
    if not workspaces_dir.is_dir():
        logger.warning("Workspaces directory not found: %s", workspaces_dir)
        return 0

    candidates: list[tuple[float, Path]] = []

    for session_dir in workspaces_dir.iterdir():
        if not session_dir.is_dir():
            continue
        if session_dir.name.startswith("."):
            continue
        # Skip if already has a dossier
        if (session_dir / "context_brief.md").exists():
            continue
        # Must have at least a run.log or transcript to be worth analyzing
        has_content = (
            (session_dir / "run.log").exists()
            or (session_dir / "transcript.md").exists()
            or (session_dir / "run_checkpoint.json").exists()
        )
        if not has_content:
            continue
        try:
            mtime = session_dir.stat().st_mtime
        except Exception:
            mtime = 0.0
        candidates.append((mtime, session_dir))

    # Process newest first
    candidates.sort(key=lambda x: x[0], reverse=True)
    candidates = candidates[:max_sessions]

    if not candidates:
        logger.info("No sessions need dossier backfill")
        return 0

    logger.info("Backfilling dossiers for %d sessions", len(candidates))
    count = 0

    for _mtime, session_dir in candidates:
        try:
            await generate_session_dossier(
                workspace=session_dir,
                metadata={"backfill": True, "session_id": session_dir.name},
            )
            count += 1
            logger.info("Backfilled dossier for %s (%d/%d)", session_dir.name, count, len(candidates))
        except Exception as exc:
            logger.warning("Backfill failed for %s: %s", session_dir.name, exc)

        # Rate-limit to avoid overwhelming the LLM API
        await asyncio.sleep(1.0)

    logger.info("Dossier backfill complete: %d/%d sessions processed", count, len(candidates))
    return count
