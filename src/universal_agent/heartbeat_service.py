
import asyncio
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Callable, Any

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.gateway import InProcessGateway, GatewaySession, GatewayRequest
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent import task_hub
from universal_agent.utils.json_utils import extract_json_payload
from universal_agent.utils.heartbeat_findings_schema import HeartbeatFindings
import shutil


try:
    import logfire
    _LOGFIRE_AVAILABLE = bool(os.getenv("LOGFIRE_TOKEN") or os.getenv("LOGFIRE_WRITE_TOKEN"))
except ImportError:
    logfire = None  # type: ignore
    _LOGFIRE_AVAILABLE = False

logger = logging.getLogger(__name__)

import hashlib
import re

import pytz

# Constants
PROJECT_ROOT = Path(__file__).parent.parent.parent
GLOBAL_HEARTBEAT_PATH = PROJECT_ROOT / "memory" / "HEARTBEAT.md"

HEARTBEAT_FILE = "HEARTBEAT.md"
HEARTBEAT_STATE_FILE = "heartbeat_state.json"
DEFAULT_HEARTBEAT_PROMPT = (
    # Keep it short and avoid encouraging the model to invent/rehash "open loops"
    # from prior chat context. HEARTBEAT.md should be the canonical checklist.
    "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. "
    "Checkbox meaning: '- [ ]' = ACTIVE/PENDING, '- [x]' = COMPLETED/DISABLED. "
    "Do not infer or repeat old tasks from prior chats. "
    "If you need to send an email, use the native `mcp__internal__send_agentmail` tool. "
    "If you need to create tasks, use the native `mcp__internal__task_hub_task_action` tool. "
    "Do NOT write or run Python/Bash scripts to interact with AgentMail or Task Hub. "
    "If nothing needs attention, reply HEARTBEAT_OK."
)

_GLOBAL_HEARTBEAT_SESSION_PREFIXES = (
    "session_hook_simone_heartbeat_",
)
INVESTIGATION_ONLY_PROMPT_INSTRUCTIONS = (
    "Investigation-only mode: do not modify repository source files or run mutating shell commands. "
    "If you draft code, write artifacts under work_products/ or UA_ARTIFACTS_DIR only."
)
DEFAULT_INTERVAL_SECONDS = 30 * 60  # 30 minutes default


def _resolve_min_interval_seconds(default: int = 30 * 60) -> int:
    return max(
        1,
        int(os.getenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", str(default)) or str(default)),
    )


MIN_INTERVAL_SECONDS = _resolve_min_interval_seconds()  # Import-time fallback; prefer runtime helper usage.
DEFAULT_HEARTBEAT_RETRY_BASE_SECONDS = max(
    1,
    int(os.getenv("UA_HEARTBEAT_RETRY_BASE_SECONDS", "10") or 10),
)
DEFAULT_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS = max(
    DEFAULT_HEARTBEAT_RETRY_BASE_SECONDS,
    int(os.getenv("UA_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS", "3600") or 3600),
)
DEFAULT_HEARTBEAT_CONTINUATION_DELAY_SECONDS = max(
    1,
    int(os.getenv("UA_HEARTBEAT_CONTINUATION_DELAY_SECONDS", "1") or 1),
)
DEFAULT_HEARTBEAT_EXEC_TIMEOUT = 1600
MIN_HEARTBEAT_EXEC_TIMEOUT = 600
DEFAULT_ACK_MAX_CHARS = 300
DEFAULT_OK_TOKENS = ["UA_HEARTBEAT_OK", "HEARTBEAT_OK"]
DEFAULT_FOREGROUND_COOLDOWN_SECONDS = max(
    0,
    int(os.getenv("UA_HEARTBEAT_FOREGROUND_COOLDOWN_SECONDS", "1800") or 1800),
)
DEFAULT_HEARTBEAT_AUTONOMOUS_ENABLED = (
    str(os.getenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
)
DEFAULT_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE = max(
    1,
    int(os.getenv("UA_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE", "5") or 5),  # Simone-first: claim up to 5 for batch triage
)
DEFAULT_HEARTBEAT_MAX_ACTIONABLE = max(
    1,
    int(os.getenv("UA_HEARTBEAT_MAX_ACTIONABLE", "50") or 50),
)
DEFAULT_HEARTBEAT_MAX_SYSTEM_EVENTS = max(
    1,
    int(os.getenv("UA_HEARTBEAT_MAX_SYSTEM_EVENTS", "25") or 25),
)

# Specialized prompt for exec completion events (Clawdbot parity)
EXEC_EVENT_PROMPT = (
    "An async command you ran earlier has completed. The result is shown in the system messages above. "
    "Please relay the command output to the user in a helpful way. If the command succeeded, share the relevant output. "
    "If it failed, explain what went wrong."
)

# Type aliases for service callbacks
SystemEventProvider = Callable[[str], list[dict]]  # (session_id) -> list of event dicts
HeartbeatEventSink = Callable[[dict], None]

@dataclass
class HeartbeatDeliveryConfig:
    mode: str = "last"  # last | explicit | none
    explicit_session_ids: list[str] = field(default_factory=list)

@dataclass
class HeartbeatVisibilityConfig:
    show_ok: bool = False
    show_alerts: bool = True
    dedupe_window_seconds: int = 86400  # 24 hours
    use_indicator: bool = False


@dataclass
class HeartbeatScheduleConfig:
    every_seconds: int = DEFAULT_INTERVAL_SECONDS
    active_start: Optional[str] = None  # "HH:MM"
    active_end: Optional[str] = None  # "HH:MM"
    timezone: str = os.getenv("USER_TIMEZONE", "America/Chicago")
    require_file: bool = False
    prompt: str = DEFAULT_HEARTBEAT_PROMPT
    ack_max_chars: int = DEFAULT_ACK_MAX_CHARS
    ok_tokens: list[str] = field(default_factory=lambda: DEFAULT_OK_TOKENS.copy())

@dataclass
class HeartbeatState:
    last_run: float = 0.0
    last_message_hash: Optional[str] = None
    last_message_ts: float = 0.0
    last_summary: Optional[dict] = None
    retry_attempt: int = 0
    next_retry_at: float = 0.0
    retry_reason: Optional[str] = None
    retry_kind: Optional[str] = None
    last_retry_delay_seconds: float = 0.0
    recent_topics: Optional[list[dict]] = None
    
    def to_dict(self):
        return {
            "last_run": self.last_run,
            "last_message_hash": self.last_message_hash,
            "last_message_ts": self.last_message_ts,
            "last_summary": self.last_summary,
            "retry_attempt": self.retry_attempt,
            "next_retry_at": self.next_retry_at,
            "retry_reason": self.retry_reason,
            "retry_kind": self.retry_kind,
            "last_retry_delay_seconds": self.last_retry_delay_seconds,
            "recent_topics": self.recent_topics or [],
        }

    @classmethod
    def from_dict(cls, data: dict):
        obj = cls()
        obj.last_run = data.get("last_run", 0.0)
        obj.last_message_hash = data.get("last_message_hash")
        obj.last_message_ts = data.get("last_message_ts", 0.0)
        obj.last_summary = data.get("last_summary")
        obj.retry_attempt = int(data.get("retry_attempt", 0) or 0)
        obj.next_retry_at = float(data.get("next_retry_at", 0.0) or 0.0)
        obj.retry_reason = data.get("retry_reason")
        obj.retry_kind = data.get("retry_kind")
        obj.last_retry_delay_seconds = float(data.get("last_retry_delay_seconds", 0.0) or 0.0)
        obj.recent_topics = data.get("recent_topics", [])
        return obj


def _parse_duration_seconds(raw: str | None, default: int) -> int:
    if not raw:
        return default
    value = raw.strip().lower()
    if not value:
        return default
    match = re.match(r"^(\d+)([smhd]?)$", value)
    if not match:
        return default
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 3600
    if unit == "d":
        return amount * 86400
    return default


def _session_prefers_global_heartbeat(session: GatewaySession) -> bool:
    session_id = str(getattr(session, "session_id", "") or "").strip()
    if any(session_id.startswith(prefix) for prefix in _GLOBAL_HEARTBEAT_SESSION_PREFIXES):
        return True
    metadata = getattr(session, "metadata", {}) or {}
    if isinstance(metadata, dict):
        source = str(metadata.get("source") or metadata.get("run_source") or "").strip().lower()
        if source == "heartbeat":
            return True
    return False


def _sync_heartbeat_file(target_path: Path, source_path: Path) -> bool:
    if not source_path.exists():
        return False
    source_text = source_path.read_text(encoding="utf-8")
    current_text = None
    if target_path.exists():
        current_text = target_path.read_text(encoding="utf-8")
    if current_text == source_text:
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(source_text, encoding="utf-8")
    return True


def _resolve_heartbeat_interval_env(
    *,
    prefer_interval: bool = True,
    warn_on_conflict: bool = False,
) -> str | None:
    interval_raw = (os.getenv("UA_HEARTBEAT_INTERVAL") or "").strip()
    every_raw = (os.getenv("UA_HEARTBEAT_EVERY") or "").strip()
    if interval_raw and every_raw and interval_raw != every_raw and warn_on_conflict:
        primary = "UA_HEARTBEAT_INTERVAL" if prefer_interval else "UA_HEARTBEAT_EVERY"
        logger.warning(
            "Conflicting heartbeat interval env vars detected; using %s. "
            "Keep only UA_HEARTBEAT_INTERVAL for clarity.",
            primary,
        )
    if prefer_interval:
        return interval_raw or every_raw or None
    return every_raw or interval_raw or None


def _heartbeat_interval_source_label(overrides: Optional[dict[str, Any]] = None) -> str:
    schedule_overridden = False
    if isinstance(overrides, dict):
        for block in (overrides, overrides.get("heartbeat"), overrides.get("schedule")):
            if not isinstance(block, dict):
                continue
            if any(str(block.get(key) or "").strip() for key in ("every", "every_seconds", "interval")):
                schedule_overridden = True
                break
    if schedule_overridden:
        return "workspace_override"
    if str(os.getenv("UA_HEARTBEAT_INTERVAL") or "").strip():
        return "UA_HEARTBEAT_INTERVAL"
    if str(os.getenv("UA_HEARTBEAT_EVERY") or "").strip():
        return "UA_HEARTBEAT_EVERY"
    return "default"


def _resolve_heartbeat_investigation_only(default: bool = False) -> bool:
    raw = os.getenv("UA_HEARTBEAT_INVESTIGATION_ONLY")
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _build_heartbeat_environment_context(workspace_dir: str) -> str:
    """Build environment context so heartbeat agents know where they are and how to write files.

    This is factory-aware: when multiple factories exist (VPS HQ, desktop workers,
    standalone nodes), the agent sees its own machine identity rather than a
    hardcoded "you are on the VPS" instruction.
    """
    from universal_agent.runtime_role import resolve_machine_slug, resolve_factory_role
    import socket

    machine_slug = resolve_machine_slug()
    factory_role = resolve_factory_role().value
    hostname = socket.gethostname()

    lines = [
        "## Heartbeat Environment Context",
        f"- Factory: {machine_slug} (role={factory_role}, host={hostname})",
        f"- Run workspace: {workspace_dir}",
        "- You are running LOCALLY on this machine. Do NOT SSH to it — run shell commands directly.",
        "",
        "### File Write Rules (MANDATORY)",
        f"- Write all output files to `{workspace_dir}/work_products/` using `mcp__internal__write_text_file`.",
        "- Do NOT use the native `Write` tool for new files (it requires a prior Read and will fail).",
        "- Do NOT write to paths outside the run workspace (they will be blocked by workspace guards).",
        "- Issue file write calls SEQUENTIALLY, not in parallel — sibling failures cascade and waste tool budget.",
        "",
        "### Structured Findings Output (MANDATORY)",
        f"- You MUST always write `{workspace_dir}/work_products/heartbeat_findings_latest.json`.",
        "- If everything is healthy and no issues are found, write:",
        '  `{"version": 1, "overall_status": "ok", "summary": "200 OK", "findings": []}`',
        "- If issues are found, write the full findings with overall_status set to 'warn' or 'critical'.",
        "- This file must be written at the END of every heartbeat run, no exceptions.",
        "",
        "### Health Check Efficiency",
        "- Combine multiple shell health checks into a single compound Bash command where possible.",
        "- Example: `uptime && echo '---' && free -h && echo '---' && df -h /`",
    ]
    return "\n".join(lines)


def _build_task_focused_environment_context(workspace_dir: str) -> str:
    """Build a lean environment context for task-focused heartbeat runs.

    When the heartbeat is dispatching Task Hub work (e.g. email-triggered tasks),
    the agent needs workspace/file-write rules but NOT system monitoring instructions.
    Health check findings are written deterministically by Python after the run.
    """
    from universal_agent.runtime_role import resolve_machine_slug, resolve_factory_role
    import socket

    machine_slug = resolve_machine_slug()
    factory_role = resolve_factory_role().value
    hostname = socket.gethostname()

    lines = [
        "## Task Execution Environment",
        f"- Factory: {machine_slug} (role={factory_role}, host={hostname})",
        f"- Run workspace: {workspace_dir}",
        "- You are running LOCALLY on this machine. Do NOT SSH to it — run shell commands directly.",
        "",
        "### File Write Rules (MANDATORY)",
        f"- Write all output files to `{workspace_dir}/work_products/` using `mcp__internal__write_text_file`.",
        "- Do NOT use the native `Write` tool for new files (it requires a prior Read and will fail).",
        "- Do NOT write to paths outside the run workspace (they will be blocked by workspace guards).",
        "- Issue file write calls SEQUENTIALLY, not in parallel — sibling failures cascade and waste tool budget.",
        "",
        "### IMPORTANT: No System Monitoring",
        "- Do NOT write heartbeat_findings_latest.json — the system handles this automatically.",
        "- Do NOT run health checks (uptime, free, df, etc.).",
        "- Do NOT write system_health_latest.md.",
        "- Focus 100% on executing the tasks below.",
    ]
    return "\n".join(lines)


def _compose_heartbeat_prompt(
    base_prompt: str,
    *,
    investigation_only: bool,
    task_hub_claims: list[dict[str, Any]],
    workspace_dir: str = "",
    brainstorm_context_text: str = "",
    morning_report_text: str = "",
    recent_topics_text: str = "",
    runtime_conn: Any = None,
    task_focused: bool = False,
) -> str:
    prompt = (base_prompt or DEFAULT_HEARTBEAT_PROMPT).strip()
    if "{ok_token}" in prompt:
        # Placeholder replacement happens separately where schedule.ok_tokens is available.
        pass
    # Inject environment context so agents know where they run and how to write files.
    if workspace_dir:
        if task_focused:
            env_context = _build_task_focused_environment_context(workspace_dir)
        else:
            env_context = _build_heartbeat_environment_context(workspace_dir)
        prompt = f"{prompt}\n\n{env_context}"
    if investigation_only and "investigation-only mode" not in prompt.lower():
        prompt = f"{prompt} {INVESTIGATION_ONLY_PROMPT_INSTRUCTIONS}".strip()
    if task_hub_claims:
        # ── Simone-First Batch Triage Prompt ──
        # Show Simone all claimed tasks and let her decide triage.
        lines = ["== TASK QUEUE TRIAGE =="]
        lines.append(
            f"You have {len(task_hub_claims)} task(s) to triage. "
            "For each, decide: SELF, DELEGATE_CODIE, DELEGATE_ATLAS, or DEFER."
        )
        lines.append("")
        for idx, item in enumerate(task_hub_claims, 1):
            title = str(item.get("title") or "(untitled)").strip()
            priority = f"P{item.get('priority', '?')}"
            source = str(item.get("source_kind") or "unknown").strip()
            score = f"{float(item.get('score', 0)):.1f}"
            task_id = str(item.get("task_id") or "")
            description = str(item.get("description") or "").strip()
            description = str(item.get("description") or "").strip()
            desc_preview = (description[:2000] + "…") if len(description) > 2000 else description
            lines.append(f"Task {idx}: [{task_id}] {title}")
            lines.append(f"  Priority: {priority} | Source: {source} | Score: {score}")
            if desc_preview:
                lines.append(f"  Description: {desc_preview}")
            
            # If there's useful metadata (like email sender info), include it briefly
            metadata = item.get("metadata_json")
            if metadata:
                import json
                try:
                    meta_dict = json.loads(metadata) if isinstance(metadata, str) else metadata
                    if isinstance(meta_dict, dict):
                        # Filter out huge fields, just give context
                        safe_meta = {k: v for k, v in meta_dict.items() if len(str(v)) < 500}
                        if safe_meta:
                            lines.append(f"  Context/Metadata: {json.dumps(safe_meta)}")
                except Exception:
                    pass
        lines.append("")
        lines.append("## Triage Protocol")
        lines.append("1. Review ALL tasks above before acting on any.")
        lines.append("2. Pick ONE task for yourself (SELF) — the one that benefits most from your")
        lines.append("   full orchestration capabilities (skills, MCPs, sub-agents, context).")
        lines.append("3. For remaining tasks, decide:")
        lines.append("   - DELEGATE_CODIE: Code-heavy — implementation, refactoring, debugging")
        lines.append("   - DELEGATE_ATLAS: Research, content generation, analysis")
        lines.append("   - DEFER: Too nuanced, needs your context, or should wait")
        lines.append("4. For each DELEGATE decision:")
        lines.append("   a) Extract ONLY the core task objective and dispatch via `vp_dispatch_mission` FIRST.")
        lines.append("      NOTE: Do NOT copy/paste these orchestration instructions into the VP objective.")
        lines.append("   b) Then call `mcp__internal__task_hub_task_action` with action=`delegate`, reason=<vp_id>,")
        lines.append("      and include `note` with the mission_id so the task tracks the VP work.")
        lines.append("      Example note: 'mission_id=<returned_id>'")
        lines.append("5. For DEFER items, use `mcp__internal__task_hub_task_action` with action=`review` to release them back.")
        lines.append("6. Then work your SELF task to completion.")
        lines.append("7. Before finishing, disposition every claimed task using `mcp__internal__task_hub_task_action`:")
        lines.append("   - `complete`: The task meaningfully fulfills the original request in spirit.")
        lines.append("     A complete result means the requested work product was produced AND delivered.")
        lines.append("     80% completion is acceptable when full completion isn't feasible.")
        lines.append("     But if the request was 'send X by email' and email wasn't sent, that's NOT complete.")
        lines.append("   - `review`: Needs human attention, rework, or quality doesn't meet the bar.")
        lines.append("   - `block`: Waiting on an external dependency you cannot resolve.")
        lines.append("   - `park`: Should be deferred indefinitely.")
        lines.append("   Do NOT leave items in `in_progress`.")

        # ── Live VP Capacity Snapshot ──
        # Shows Simone whether VPs have bandwidth for delegation
        if runtime_conn is not None:
            try:
                import os as _os
                max_coder = int(_os.getenv("UA_MAX_CONCURRENT_VP_CODER", "1"))
                max_general = int(_os.getenv("UA_MAX_CONCURRENT_VP_GENERAL", "2"))
                # Count currently delegated tasks per VP type
                _delegated_rows = runtime_conn.execute(
                    "SELECT metadata_json FROM task_hub_items WHERE status = 'delegated'"
                ).fetchall()
                active_coder = 0
                active_general = 0
                for _dr in _delegated_rows:
                    _meta_raw = _dr[0] if not hasattr(_dr, "keys") else _dr["metadata_json"]
                    try:
                        _meta = json.loads(_meta_raw) if isinstance(_meta_raw, str) else (_meta_raw or {})
                        _target = str((_meta.get("delegation") or {}).get("delegate_target", "")).lower()
                        if "coder" in _target:
                            active_coder += 1
                        else:
                            active_general += 1
                    except Exception:
                        active_general += 1  # assume general if unknown

                lines.append("")
                lines.append("## VP Capacity (live)")
                lines.append(f"  Atlas (vp.general.primary): {active_general}/{max_general} slots in use")
                lines.append(f"  Codie (vp.coder.primary):   {active_coder}/{max_coder} slots in use")
                if active_general < max_general or active_coder < max_coder:
                    lines.append("  → Delegation IS available. Use it for disparate tasks.")
                else:
                    lines.append("  → All VP slots occupied. DEFER delegation or process yourself.")
            except Exception as _cap_exc:
                logger.debug("VP capacity injection failed: %s", _cap_exc)

        # ── Delegation Strategy Guidance ──
        lines.append("")
        lines.append("## Delegation Strategy")
        lines.append("When you have multiple tasks, analyze their dependencies:")
        lines.append("- PARALLEL: Tasks are independent (different topics, no shared context)")
        lines.append("  → Dispatch each to a separate VP agent simultaneously")
        lines.append("  → Achieves N× throughput vs. serial processing")
        lines.append("  → Example: 'Research X' + 'Draft email Y' + 'Update doc Z'")
        lines.append("- SEQUENTIAL: Tasks feed into each other (output of A is input to B)")
        lines.append("  → Keep in one agent (usually yourself) to preserve context")
        lines.append("  → Example: 'Analyze data' then 'Write report based on analysis'")
        lines.append("- BATCH: Related micro-tasks from same domain")
        lines.append("  → Group and send as one VP mission for efficiency")
        lines.append("  → Example: 'Update 3 config files' → single Codie mission")
        lines.append("If a task email contains multiple distinct items, use `task_hub_decompose`")
        lines.append("to split it into linked sub-tasks, then delegate each independently.")

        task_ids = sorted({str(item.get("task_id") or "").strip() for item in task_hub_claims if str(item.get("task_id") or "").strip()})
        lines.append(f"\nClaimed task_ids: {', '.join(task_ids) if task_ids else '(none)'}")
        prompt = f"{prompt}\n\n" + "\n".join(lines)

    # ── Phase 4: VP Completion Review Prompt ──
    # Inject pending_review tasks for Simone's sign-off
    if runtime_conn is not None:
        try:
            from universal_agent.task_hub import get_pending_review_tasks, reopen_stale_delegations
            _runtime_conn = runtime_conn
            if _runtime_conn is not None:
                # 1. Reopen stale delegations (>4h without VP progress)
                stale_reopened = reopen_stale_delegations(_runtime_conn, stale_hours=4.0)
                if stale_reopened:
                    stale_lines = ["\n== STALE DELEGATION RECOVERY =="]
                    stale_lines.append(f"{len(stale_reopened)} task(s) reopened due to >4h VP inactivity:")
                    for st in stale_reopened:
                        stale_lines.append(f"  - [{st.get('task_id','')}] {st.get('title', '(untitled)')}")
                    stale_lines.append("These are back in the open queue for re-triage.")
                    prompt = f"{prompt}\n" + "\n".join(stale_lines)

                # 2. Show pending_review tasks for sign-off
                pending_reviews = get_pending_review_tasks(_runtime_conn)
                if pending_reviews:
                    review_lines = ["\n== VP COMPLETION REVIEW =="]
                    review_lines.append(f"{len(pending_reviews)} VP-completed task(s) await your sign-off:\n")
                    for idx, pr in enumerate(pending_reviews, 1):
                        pr_id = str(pr.get("task_id") or "")
                        pr_title = str(pr.get("title") or "(untitled)").strip()
                        delegation = dict(pr.get("metadata") or {}).get("delegation") or {}
                        vp_status = delegation.get("vp_terminal_status", "?")
                        vp_id = delegation.get("vp_id", "?")
                        mission_id = delegation.get("mission_id", "?")
                        result_summary = delegation.get("result_summary", "")
                        review_lines.append(f"Review {idx}: [{pr_id}] {pr_title}")
                        review_lines.append(f"  VP: {vp_id} | Status: {vp_status} | Mission: {mission_id}")
                        if result_summary:
                            review_lines.append(f"  Summary: {result_summary[:150]}")
                    review_lines.append("")
                    review_lines.append("## Review Protocol")
                    review_lines.append("For each pending review item:")
                    review_lines.append("1. If the VP mission succeeded (status=completed):")
                    review_lines.append("   - Read the artifacts via `vp_read_result_artifacts(mission_id=...)`")
                    review_lines.append("   - Evaluate: Does this meaningfully fulfill the original request in spirit?")
                    review_lines.append("     • Was the requested work product actually produced?")
                    review_lines.append("     • Was it delivered to the intended destination (email, Slack, file)?")
                    review_lines.append("     • Is quality sufficient — not perfect, but meeting the core intent?")
                    review_lines.append("   - If yes → `mcp__internal__task_hub_task_action(action='approve', task_id=...)`")
                    review_lines.append("   - If deliverables miss the mark → `mcp__internal__task_hub_task_action(action='review',")
                    review_lines.append("     task_id=..., note='rework needed: <specific gap>')` to re-open")
                    review_lines.append("2. If the VP mission failed/cancelled:")
                    review_lines.append("   - `mcp__internal__task_hub_task_action(action='review', task_id=..., note='VP failed, needs retry')`")
                    review_lines.append("   This puts the task back into the open queue for re-triage.")
                    review_lines.append("3. Complete ALL reviews BEFORE starting new task work.")
                    prompt = f"{prompt}\n" + "\n".join(review_lines)
        except Exception as _review_exc:
            logger.debug("VP review prompt injection skipped: %s", _review_exc)

    # Inject brainstorm context so the agent is aware of refinement stages
    # Skip brainstorm and morning report in task-focused mode — they add noise
    if brainstorm_context_text and not task_focused:
        prompt = f"{prompt}\n\n{brainstorm_context_text}"
    # Inject morning report if this is the first tick of the day
    if morning_report_text and not task_focused:
        prompt = f"{prompt}\n\n{morning_report_text}"
    # Inject recent topics history to prevent loops
    if recent_topics_text and not task_focused:
        prompt = f"{prompt}\n\n{recent_topics_text}"
    return prompt

def _parse_active_hours(raw: str | None) -> tuple[Optional[str], Optional[str]]:
    if not raw:
        return None, None
    cleaned = raw.strip()
    if not cleaned:
        return None, None
    if "-" not in cleaned:
        return None, None
    start, end = [part.strip() for part in cleaned.split("-", 1)]
    return start or None, end or None


def _parse_hhmm(raw: str | None, allow_24: bool) -> Optional[int]:
    if not raw:
        return None
    match = re.match(r"^([01]\d|2[0-3]|24):([0-5]\d)$", raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour == 24 and (not allow_24 or minute != 0):
        return None
    return hour * 60 + minute


def _resolve_active_timezone(raw: str | None) -> str:
    candidate = (raw or "").strip()
    if not candidate:
        return os.getenv("USER_TIMEZONE", "America/Chicago")
    if candidate.lower() == "user":
        return os.getenv("USER_TIMEZONE", "America/Chicago")
    if candidate.lower() == "local":
        try:
            return datetime.now().astimezone().tzinfo.key  # type: ignore[attr-defined]
        except Exception:
            return os.getenv("USER_TIMEZONE", "America/Chicago")
    return candidate


def _minutes_in_timezone(now_ts: float, tz_name: str) -> Optional[int]:
    try:
        tz = pytz.timezone(tz_name)
        now = datetime.fromtimestamp(now_ts, tz)
        return now.hour * 60 + now.minute
    except Exception:
        return None


def _parse_iso_to_unix(value: object) -> Optional[float]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _within_active_hours(cfg: HeartbeatScheduleConfig, now_ts: float) -> bool:
    start_min = _parse_hhmm(cfg.active_start, allow_24=False)
    end_min = _parse_hhmm(cfg.active_end, allow_24=True)
    if start_min is None or end_min is None:
        return True
    if start_min == end_min:
        return True
    current_min = _minutes_in_timezone(now_ts, cfg.timezone)
    if current_min is None:
        return True
    if end_min > start_min:
        return start_min <= current_min < end_min
    return current_min >= start_min or current_min < end_min


def _is_effectively_empty(content: str) -> bool:
    lines = content.split("\n")
    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            continue
        if re.match(r"^#+(\s|$)", trimmed):
            continue
        if re.match(r"^[-*+]\s*(\[[\sXx]?\]\s*)?$", trimmed):
            continue
        return False
    return True


def _strip_markup_edges(text: str) -> str:
    return (
        re.sub(r"<[^>]*>", " ", text)
        .replace("&nbsp;", " ")
        .strip("*`~_ ")
    )


def _strip_token_at_edges(text: str, token: str) -> tuple[str, bool]:
    value = text.strip()
    if token not in value:
        return value, False
    did_strip = False
    changed = True
    while changed:
        changed = False
        next_value = value.strip()
        if next_value.startswith(token):
            value = next_value[len(token):].lstrip()
            did_strip = True
            changed = True
            continue
        if next_value.endswith(token):
            value = next_value[: max(0, len(next_value) - len(token))].rstrip()
            did_strip = True
            changed = True
    return re.sub(r"\s+", " ", value).strip(), did_strip


def _strip_heartbeat_tokens(text: str, tokens: list[str], max_ack_chars: int) -> dict:
    if not text or not text.strip():
        return {"ok_only": True, "text": "", "token": None}
    raw = text.strip()
    normalized = _strip_markup_edges(raw)
    tokens_sorted = sorted(tokens, key=len, reverse=True)
    # Heuristic: if a known OK token appears anywhere AND the surrounding text
    # is clearly a no-op checklist/summary, treat as OK-only to avoid accidental
    # unsuppressed "wall of text" no-op heartbeats.
    noop_markers = [
        "no tasks match current conditions",
        "checking heartbeat.md tasks",
        "no tasks match current condition",
        "no tasks match",
    ]
    normalized_lower = normalized.lower()
    for token in tokens_sorted:
        if token in raw or token in normalized:
            if any(marker in normalized_lower for marker in noop_markers):
                return {"ok_only": True, "text": "", "token": token}

        if token not in raw and token not in normalized:
            continue
        stripped_raw, did_raw = _strip_token_at_edges(raw, token)
        stripped_norm, did_norm = _strip_token_at_edges(normalized, token)
        candidate = stripped_raw if did_raw and stripped_raw else stripped_norm
        did_strip = did_raw or did_norm
        if not did_strip:
            continue
        if not candidate:
            return {"ok_only": True, "text": "", "token": token}
        if len(candidate) <= max_ack_chars:
            return {"ok_only": True, "text": "", "token": token}
        return {"ok_only": False, "text": candidate, "token": token}
    return {"ok_only": False, "text": raw, "token": None}


def _parse_ok_tokens(raw: Optional[str]) -> list[str]:
    if raw:
        tokens = [t.strip() for t in re.split(r"[,\n]", raw) if t.strip()]
        if tokens:
            return tokens
    return DEFAULT_OK_TOKENS.copy()


def _parse_int(raw: Optional[str], default: int) -> int:
    if not raw:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _parse_bool(raw: Optional[str], default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_exec_timeout_seconds() -> int:
    timeout = _parse_int(os.getenv("UA_HEARTBEAT_EXEC_TIMEOUT"), DEFAULT_HEARTBEAT_EXEC_TIMEOUT)
    if timeout < MIN_HEARTBEAT_EXEC_TIMEOUT:
        logger.warning(
            "UA_HEARTBEAT_EXEC_TIMEOUT=%s is too low for current heartbeat workloads; using %ss",
            timeout,
            MIN_HEARTBEAT_EXEC_TIMEOUT,
        )
        return MIN_HEARTBEAT_EXEC_TIMEOUT
    return timeout


def _heartbeat_guard_policy(
    *,
    actionable_count: Optional[int],
    brainstorm_candidate_count: int,
    system_event_count: int,
    has_exec_completion: bool,
    has_heartbeat_content: bool = False,
    pending_question_count: int = 0,
) -> dict[str, object]:
    autonomous_enabled = _parse_bool(
        os.getenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED"),
        default=DEFAULT_HEARTBEAT_AUTONOMOUS_ENABLED,
    )
    max_actionable = max(
        1,
        _parse_int(os.getenv("UA_HEARTBEAT_MAX_ACTIONABLE"), DEFAULT_HEARTBEAT_MAX_ACTIONABLE),
    )
    max_system_events = max(
        1,
        _parse_int(os.getenv("UA_HEARTBEAT_MAX_SYSTEM_EVENTS"), DEFAULT_HEARTBEAT_MAX_SYSTEM_EVENTS),
    )
    max_proactive_per_cycle = max(
        1,
        _parse_int(
            os.getenv("UA_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE"),
            DEFAULT_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE,
        ),
    )
    actionable = int(actionable_count or 0)

    skip_reason: Optional[str] = None
    if actionable_count is not None and actionable > max_actionable:
        skip_reason = "actionable_over_capacity"
    elif (
        not autonomous_enabled
        and not has_exec_completion
        and system_event_count <= 0
        and pending_question_count <= 0
        and (actionable > 0 or brainstorm_candidate_count > 0)
    ):
        skip_reason = "autonomous_disabled"
    elif (
        actionable_count is not None
        and actionable <= 0
        and brainstorm_candidate_count <= 0
        and system_event_count <= 0
        and not has_exec_completion
        and not has_heartbeat_content
        and pending_question_count <= 0
    ):
        # Phase 1: Overnight reflection mode — instead of always sleeping when
        # the queue is empty, check if we're in the reflection window and the
        # engine is enabled.  If so, the agent runs in reflection mode to
        # generate and work on autonomous tasks.
        _reflection_mode = False
        try:
            from universal_agent.services.reflection_engine import (
                is_reflection_enabled,
                is_reflection_hours,
            )
            if is_reflection_enabled() and is_reflection_hours():
                _reflection_mode = True
                skip_reason = None  # Don't skip — run reflection mode
                logger.info("Reflection mode activated: queue empty but within overnight window")
            else:
                skip_reason = "no_actionable_work"
        except Exception:
            skip_reason = "no_actionable_work"

    return {
        "autonomous_enabled": autonomous_enabled,
        "max_actionable": max_actionable,
        "max_system_events": max_system_events,
        "max_proactive_per_cycle": max_proactive_per_cycle,
        "pending_question_count": pending_question_count,
        "skip_reason": skip_reason,
        "reflection_mode": _reflection_mode if "_reflection_mode" in dir() else False,
    }


def _coerce_bool(value: Optional[object], default: Optional[bool] = None) -> Optional[bool]:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _coerce_int(value: Optional[object], default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def _coerce_list(value: Optional[object]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
    value_str = str(value).strip()
    return [value_str] if value_str else []


def _load_json_overrides(workspace: Path) -> dict:
    for name in ("HEARTBEAT.json", "heartbeat.json", ".heartbeat.json"):
        path = workspace / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            return {}
        if isinstance(payload, dict):
            return payload
        logger.warning("Heartbeat override file %s is not a JSON object", path)
        return {}
    return {}


def _persist_heartbeat_state(state_path: Path, state: HeartbeatState) -> None:
    with open(state_path, "w") as f:
        json.dump(state.to_dict(), f)


def _heartbeat_retry_delay_seconds(
    attempt: int,
    *,
    base_seconds: int,
    max_backoff_seconds: int,
) -> int:
    bounded_attempt = max(1, int(attempt or 1))
    return min(base_seconds * (2 ** (bounded_attempt - 1)), max_backoff_seconds)

class HeartbeatService:
    def __init__(
        self,
        gateway: InProcessGateway,
        connection_manager,
        system_event_provider: Optional[SystemEventProvider] = None,
        event_sink: Optional[HeartbeatEventSink] = None,
        heartbeat_scope: str = "global",
    ):
        self.gateway = gateway
        self.connection_manager = connection_manager
        self.system_event_provider = system_event_provider
        self.event_sink = event_sink
        self.heartbeat_scope = heartbeat_scope
        self.execution_timeout_seconds = _resolve_exec_timeout_seconds()
        self.retry_base_seconds = max(
            1,
            _parse_int(os.getenv("UA_HEARTBEAT_RETRY_BASE_SECONDS"), DEFAULT_HEARTBEAT_RETRY_BASE_SECONDS),
        )
        self.max_retry_backoff_seconds = max(
            self.retry_base_seconds,
            _parse_int(
                os.getenv("UA_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS"),
                DEFAULT_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS,
            ),
        )
        self.continuation_delay_seconds = max(
            1,
            _parse_int(
                os.getenv("UA_HEARTBEAT_CONTINUATION_DELAY_SECONDS"),
                DEFAULT_HEARTBEAT_CONTINUATION_DELAY_SECONDS,
            ),
        )
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.active_sessions: Dict[str, GatewaySession] = {}
        # Simple tracking of busy sessions (primitive lock)
        self.busy_sessions: set[str] = set()
        self.wake_sessions: set[str] = set()
        self.wake_next_sessions: set[str] = set()
        self.last_wake_reason: Dict[str, str] = {}
        self.foreground_cooldown_seconds = DEFAULT_FOREGROUND_COOLDOWN_SECONDS
        
        # MOCK CONFIG (In future, load from session config)
        self.default_delivery = HeartbeatDeliveryConfig(
            mode=os.getenv("UA_HB_DELIVERY_MODE", "last"),
            explicit_session_ids=[
                s.strip()
                for s in os.getenv("UA_HB_EXPLICIT_SESSION_IDS", "").split(",")
                if s.strip()
            ],
        )
        self.default_visibility = HeartbeatVisibilityConfig(
            show_ok=os.getenv("UA_HB_SHOW_OK", "false").lower() == "true",
            show_alerts=os.getenv("UA_HB_SHOW_ALERTS", "true").lower() == "true",
            dedupe_window_seconds=int(os.getenv("UA_HB_DEDUPE_WINDOW", "86400")),
            use_indicator=os.getenv("UA_HB_USE_INDICATOR", "false").lower() == "true",
        )
        active_start = os.getenv("UA_HEARTBEAT_ACTIVE_START")
        active_end = os.getenv("UA_HEARTBEAT_ACTIVE_END")
        if os.getenv("UA_HEARTBEAT_ACTIVE_HOURS") and not (active_start or active_end):
            parsed_start, parsed_end = _parse_active_hours(os.getenv("UA_HEARTBEAT_ACTIVE_HOURS"))
            active_start = parsed_start or active_start
            active_end = parsed_end or active_end

        ok_tokens = _parse_ok_tokens(os.getenv("UA_HEARTBEAT_OK_TOKENS"))
        legacy_ok = os.getenv("UA_HEARTBEAT_OK_TOKEN") or os.getenv("UA_HEARTBEAT_OK")
        if legacy_ok:
            ok_tokens = [legacy_ok] + [t for t in ok_tokens if t != legacy_ok]

        interval_raw = _resolve_heartbeat_interval_env(
            prefer_interval=True,
            warn_on_conflict=True,
        )
        self.default_schedule = HeartbeatScheduleConfig(
            every_seconds=_parse_duration_seconds(interval_raw, DEFAULT_INTERVAL_SECONDS),
            active_start=active_start or None,
            active_end=active_end or None,
            timezone=_resolve_active_timezone(os.getenv("UA_HEARTBEAT_TIMEZONE")),
            require_file=_parse_bool(os.getenv("UA_HEARTBEAT_REQUIRE_FILE"), default=False),
            prompt=os.getenv("UA_HEARTBEAT_PROMPT", DEFAULT_HEARTBEAT_PROMPT),
            ack_max_chars=_parse_int(os.getenv("UA_HEARTBEAT_ACK_MAX_CHARS"), DEFAULT_ACK_MAX_CHARS),
            ok_tokens=ok_tokens,
        )

    def _emit_event(self, payload: dict) -> None:
        if not self.event_sink:
            return
        try:
            self.event_sink(payload)
        except Exception as exc:
            logger.warning("Heartbeat event sink failed: %s", exc)

    def _clear_retry_state(self, state: HeartbeatState) -> None:
        state.retry_attempt = 0
        state.next_retry_at = 0.0
        state.retry_reason = None
        state.retry_kind = None
        state.last_retry_delay_seconds = 0.0

    def _schedule_retry(
        self,
        state: HeartbeatState,
        *,
        session_id: str,
        now_ts: float,
        kind: str,
        reason: str,
    ) -> int:
        attempt = state.retry_attempt + 1 if state.retry_kind == kind else 1
        delay_seconds = _heartbeat_retry_delay_seconds(
            attempt,
            base_seconds=self.retry_base_seconds,
            max_backoff_seconds=self.max_retry_backoff_seconds,
        )
        state.retry_attempt = attempt
        state.next_retry_at = now_ts + delay_seconds
        state.retry_reason = reason
        state.retry_kind = kind
        state.last_retry_delay_seconds = float(delay_seconds)
        self._emit_event(
            {
                "type": "heartbeat_retry_scheduled",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "retry_kind": kind,
                "retry_attempt": attempt,
                "retry_reason": reason,
                "retry_delay_seconds": delay_seconds,
                "next_retry_at": datetime.fromtimestamp(state.next_retry_at, timezone.utc).isoformat(),
            }
        )
        return delay_seconds

    def _schedule_continuation_retry(
        self,
        state: HeartbeatState,
        *,
        now_ts: float,
        reason: str,
    ) -> None:
        state.retry_attempt = 1
        state.next_retry_at = now_ts + float(self.continuation_delay_seconds)
        state.retry_reason = reason
        state.retry_kind = "continuation"
        state.last_retry_delay_seconds = float(self.continuation_delay_seconds)

    def _consume_wake_request(self, session_id: str) -> Optional[str]:
        wake_reason = self.last_wake_reason.pop(session_id, None)
        self.wake_sessions.discard(session_id)
        self.wake_next_sessions.discard(session_id)
        return wake_reason

    def _resolve_schedule(self, overrides: dict) -> HeartbeatScheduleConfig:
        schedule = replace(self.default_schedule)
        schedule.ok_tokens = list(schedule.ok_tokens)
        schedule_data: dict = {}
        heartbeat_block = overrides.get("heartbeat")
        if isinstance(heartbeat_block, dict):
            schedule_data.update(heartbeat_block)
        if isinstance(overrides.get("schedule"), dict):
            schedule_data.update(overrides["schedule"])
        for key in (
            "every",
            "every_seconds",
            "interval",
            "active_hours",
            "active_start",
            "active_end",
            "timezone",
            "require_file",
            "prompt",
            "ack_max_chars",
            "ok_tokens",
        ):
            if key in overrides:
                schedule_data.setdefault(key, overrides[key])

        interval_raw = (
            schedule_data.get("every")
            or schedule_data.get("every_seconds")
            or schedule_data.get("interval")
        )
        if interval_raw is not None:
            schedule.every_seconds = _parse_duration_seconds(str(interval_raw), schedule.every_seconds)
        min_interval_seconds = _resolve_min_interval_seconds(default=MIN_INTERVAL_SECONDS)
        schedule.every_seconds = max(min_interval_seconds, int(schedule.every_seconds or DEFAULT_INTERVAL_SECONDS))

        active_start = schedule_data.get("active_start") or schedule_data.get("activeStart")
        active_end = schedule_data.get("active_end") or schedule_data.get("activeEnd")
        active_hours = schedule_data.get("active_hours") or schedule_data.get("activeHours")
        if active_hours and not (active_start or active_end):
            parsed_start, parsed_end = _parse_active_hours(str(active_hours))
            active_start = parsed_start or active_start
            active_end = parsed_end or active_end
        if active_start is not None:
            schedule.active_start = str(active_start)
        if active_end is not None:
            schedule.active_end = str(active_end)

        if schedule_data.get("timezone") is not None:
            schedule.timezone = _resolve_active_timezone(str(schedule_data.get("timezone")))

        require_file = _coerce_bool(schedule_data.get("require_file"))
        if require_file is not None:
            schedule.require_file = require_file

        prompt = schedule_data.get("prompt")
        if prompt is not None:
            schedule.prompt = str(prompt)

        ack_max_chars = _coerce_int(schedule_data.get("ack_max_chars"))
        if ack_max_chars is not None:
            schedule.ack_max_chars = ack_max_chars

        ok_tokens = schedule_data.get("ok_tokens") or schedule_data.get("okTokens")
        if ok_tokens is not None:
            if isinstance(ok_tokens, list):
                schedule.ok_tokens = _coerce_list(ok_tokens)
            else:
                schedule.ok_tokens = _parse_ok_tokens(str(ok_tokens))

        return schedule

    def _resolve_delivery(self, overrides: dict, session_id: str) -> HeartbeatDeliveryConfig:
        delivery = HeartbeatDeliveryConfig(
            mode=self.default_delivery.mode,
            explicit_session_ids=list(self.default_delivery.explicit_session_ids),
        )
        delivery_data: dict = {}
        if isinstance(overrides.get("delivery"), dict):
            delivery_data.update(overrides["delivery"])
        if "delivery_mode" in overrides:
            delivery_data.setdefault("mode", overrides["delivery_mode"])
        if "explicit_session_ids" in overrides:
            delivery_data.setdefault("explicit_session_ids", overrides["explicit_session_ids"])
        if "explicit" in overrides:
            delivery_data.setdefault("explicit_session_ids", overrides["explicit"])

        mode = str(delivery_data.get("mode", delivery.mode)).strip().lower()
        if mode not in {"last", "explicit", "none"}:
            logger.warning("Unknown heartbeat delivery mode '%s'; defaulting to 'last'", mode)
            mode = "last"
        delivery.mode = mode

        if delivery.mode == "explicit":
            explicit_ids = delivery_data.get("explicit_session_ids") or delivery_data.get("targets")
            if explicit_ids is not None:
                delivery.explicit_session_ids = _coerce_list(explicit_ids)

            valid_sessions = set(self.active_sessions.keys())
            cleaned: list[str] = []
            for target in delivery.explicit_session_ids:
                if target.upper() == "CURRENT":
                    cleaned.append("CURRENT")
                    continue
                if target == session_id:
                    cleaned.append(target)
                    continue
                if target in valid_sessions:
                    cleaned.append(target)
                    continue
                logger.warning("Heartbeat delivery target '%s' not active; skipping", target)
            delivery.explicit_session_ids = cleaned

        return delivery

    def _resolve_visibility(self, overrides: dict) -> HeartbeatVisibilityConfig:
        visibility = replace(self.default_visibility)
        visibility_data: dict = {}
        if isinstance(overrides.get("visibility"), dict):
            visibility_data.update(overrides["visibility"])
        for key in ("show_ok", "show_alerts", "dedupe_window_seconds", "use_indicator"):
            if key in overrides:
                visibility_data.setdefault(key, overrides[key])

        show_ok = _coerce_bool(visibility_data.get("show_ok"))
        if show_ok is not None:
            visibility.show_ok = show_ok
        show_alerts = _coerce_bool(visibility_data.get("show_alerts"))
        if show_alerts is not None:
            visibility.show_alerts = show_alerts
        dedupe_window = _coerce_int(visibility_data.get("dedupe_window_seconds"))
        if dedupe_window is not None:
            visibility.dedupe_window_seconds = dedupe_window
        use_indicator = _coerce_bool(visibility_data.get("use_indicator"))
        if use_indicator is not None:
            visibility.use_indicator = use_indicator

        return visibility

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._scheduler_loop())
        logger.info("💓 Heartbeat Service started")

    async def stop(self):
        if not self.running:
            return
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("💔 Heartbeat Service stopped")

    def register_session(self, session: GatewaySession):
        logger.info(f"Registering session {session.session_id} for heartbeat")
        # Tag the session as source=heartbeat so the gateway reaper applies
        # the correct (short) TTL for admin sessions.
        if isinstance(session.metadata, dict):
            session.metadata.setdefault("source", "heartbeat")
        self.active_sessions[session.session_id] = session

    def unregister_session(self, session_id: str):
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]

    def request_heartbeat_now(self, session_id: str, reason: str = "wake") -> None:
        self.wake_sessions.add(session_id)
        self.last_wake_reason[session_id] = reason
        self._emit_event(
            {
                "type": "heartbeat_wake_requested",
                "session_id": session_id,
                "mode": "now",
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
            }
        )
        logger.info("Heartbeat wake requested for %s (%s)", session_id, reason)

    def request_heartbeat_next(self, session_id: str, reason: str = "wake_next") -> None:
        self.wake_next_sessions.add(session_id)
        self.last_wake_reason[session_id] = reason
        self._emit_event(
            {
                "type": "heartbeat_wake_requested",
                "session_id": session_id,
                "mode": "next",
                "reason": reason,
                "timestamp": datetime.now().isoformat(),
            }
        )
        logger.info("Heartbeat wake-next requested for %s (%s)", session_id, reason)

    async def _scheduler_loop(self):
        """Main loop that checks sessions periodically."""
        logger.info("Heartbeat scheduler loop starting")
        while self.running:
            try:
                # We use a simple 10s tick for the MVP; production would use a heap
                start_time = time.time()
                
                count = len(self.active_sessions)
                if count > 0:
                    logger.debug(f"Heartbeat tick: {count} active sessions")
                    # import sys; sys.stderr.write(f"DEBUG: TICK {count}\n") # Removed noisy debug
                
                # Use list snapshot to avoid runtime errors
                for session_id, session in list(self.active_sessions.items()):
                    try:
                        await self._process_session(session)
                    except Exception as e:
                        logger.error(f"Error processing heartbeat for {session_id}: {e}")
                
                # Sleep remainder of tick (cap at 5s, but respect shorter heartbeat intervals)
                elapsed = time.time() - start_time
                # Tick interval cap increased to 30s for less noise; respects shorter intervals if configured
                tick_interval = max(1.0, min(30.0, float(self.default_schedule.every_seconds)))
                sleep_time = max(0.5, tick_interval - elapsed)
                await asyncio.sleep(sleep_time)
            except Exception as e:
                logger.critical(f"Scheduler loop crash: {e}", exc_info=True)
                await asyncio.sleep(5)

    def _check_session_idle(self, session: GatewaySession) -> bool:
        """
        Check if session is idle (no connections, no active runs, and past timeout).
        Returns True if session was unregistered (and thus processing should stop).
        """
        # Daemon sessions (persistent agent sessions) are intentionally
        # connection-less; they exist solely for proactive heartbeat dispatch.
        # Never reap them via idle timeout.
        from universal_agent.services.daemon_sessions import is_daemon_session
        if is_daemon_session(session.session_id):
            return False

        unregister_idle = _parse_bool(os.getenv("UA_HEARTBEAT_UNREGISTER_IDLE"), default=True)
        if not unregister_idle:
            return False

        # Get runtime metadata
        runtime = session.metadata.get("runtime", {})
        active_connections = int(runtime.get("active_connections", 0))
        active_runs = int(runtime.get("active_runs", 0))

        # Check legacy connection manager just in case (e.g. if metadata sync failed)
        cm_connections = 0
        if self.connection_manager and hasattr(self.connection_manager, "session_connections"):
             connections = self.connection_manager.session_connections.get(session.session_id)
             if connections:
                 cm_connections = len(connections)
        
        # If any connections exist, it's not idle
        if active_connections > 0 or cm_connections > 0:
            return False
            
        # If any runs are active, it's not idle
        if active_runs > 0:
            return False

        # Keep session registered if it has an explicit wake request queued.
        if session.session_id in self.wake_sessions or session.session_id in self.wake_next_sessions:
            return False

        # Check idle duration
        last_activity_str = runtime.get("last_activity_at")
        if not last_activity_str:
            # If no activity recorded ever, assume safe to keep or handle elsewhere
            return False
            
        try:
            # Handle Z suffix for older python versions if needed
            ts_str = str(last_activity_str).replace("Z", "+00:00")
            last_activity = datetime.fromisoformat(ts_str)
            now = datetime.now(last_activity.tzinfo) if last_activity.tzinfo else datetime.now()
            
            # Default 10 minutes (600s) for admin/heartbeat sessions
            idle_timeout = int(os.getenv("UA_HEARTBEAT_IDLE_TIMEOUT", "600"))
            
            elapsed = (now - last_activity).total_seconds()
            if elapsed > idle_timeout:
                logger.info(
                    "🧹 Unregistering idle session %s (idle for %.1fs > %ds, 0 connections)", 
                    session.session_id, elapsed, idle_timeout
                )
                self.unregister_session(session.session_id)
                return True
        except Exception as e:
            logger.warning(f"Failed to check idle state for {session.session_id}: {e}")
            
        return False

    # Session prefixes that represent ephemeral, fire-and-forget processing
    # sessions and should never receive heartbeat checks.  These sessions
    # have no HEARTBEAT.md, their agent work is self-contained, and running
    # heartbeats on them wastes LLM tokens and produces false-alarm timeout
    # notifications.
    _HEARTBEAT_EXCLUDED_PREFIXES = (
        "session_hook_yt_",       # YouTube tutorial processing
        "session_hook_simone_",   # Simone webhook listener
        "session_hook_agentmail_",  # AgentMail listener
    )

    async def _process_session(self, session: GatewaySession):
        """Check if a session needs a heartbeat run."""
        # Check for idle cleanup first
        if self._check_session_idle(session):
            return

        # Ephemeral hook sessions are fire-and-forget; skip heartbeat entirely.
        if session.session_id.startswith(self._HEARTBEAT_EXCLUDED_PREFIXES):
            return

        # Load state
        workspace = Path(session.workspace_dir)
        state_path = workspace / HEARTBEAT_STATE_FILE
        state = HeartbeatState()
        if state_path.exists():
            try:
                with open(state_path, "r") as f:
                    state = HeartbeatState.from_dict(json.load(f))
            except Exception as e:
                logger.warning(f"Failed to load heartbeat state for {session.session_id}: {e}")

        overrides = _load_json_overrides(workspace)
        schedule = self._resolve_schedule(overrides)
        delivery = self._resolve_delivery(overrides, session.session_id)
        visibility = self._resolve_visibility(overrides)
        interval_source = _heartbeat_interval_source_label(overrides)
        now = time.time()
        retry_due = state.next_retry_at > 0 and now >= state.next_retry_at
        retry_pending = state.next_retry_at > now

        # If this is a fresh state (last_run=0), align to the previous scheduled slot
        # to prevent an immediate run on startup. The heartbeat will trigger at the
        # next natural interval boundary.
        if state.last_run == 0:
             # Example: interval=1800 (30m). now=1000.
             # last_run = (1000 // 1800) * 1800 = 0 (if now < 1800) or aligned floor
             # Actually we want: last_run = now - (now % interval)
             # If now is 12:05 and interval is 30m, last_run becomes 12:00.
             # elapsed = 5m < 30m. Next run at 12:30.
             state.last_run = now - (now % schedule.every_seconds)
             # Optimization: Save this initial state so we don't recalculate on every tick if we restart
             try:
                 _persist_heartbeat_state(state_path, state)
             except Exception:
                 pass

        wake_requested = session.session_id in self.wake_sessions
        wake_next = session.session_id in self.wake_next_sessions
        queued_wake_reason = self.last_wake_reason.get(session.session_id)

        scheduled_due = (now - state.last_run) >= schedule.every_seconds
        within_active_hours = _within_active_hours(schedule, now)
        lock_reason = self._session_heartbeat_lock_reason(session, now)
        if lock_reason == "foreground_connection_active":
            lock_reason = None
        explicit_wake_bypasses_lock = (wake_requested or (wake_next and scheduled_due)) and lock_reason in {
            "foreground_run_active",
            "foreground_cooldown_active",
        }
        if explicit_wake_bypasses_lock:
            lock_reason = None
        if lock_reason:
            should_queue_retry = (wake_requested or retry_due or scheduled_due) and within_active_hours
            if should_queue_retry:
                wake_reason = self._consume_wake_request(session.session_id)
                if scheduled_due and not retry_due and not wake_reason:
                    state.last_run = now
                delay_seconds = self._schedule_retry(
                    state,
                    session_id=session.session_id,
                    now_ts=now,
                    kind="busy",
                    reason=lock_reason if not wake_reason else f"{lock_reason}:{wake_reason}",
                )
                state.last_summary = {
                    "timestamp": datetime.now().isoformat(),
                    "ok_only": True,
                    "text": None,
                    "token": None,
                    "sent": False,
                    "artifacts": {"writes": [], "work_products": [], "bash_commands": []},
                    "delivery": {
                        "mode": delivery.mode,
                        "targets": [],
                        "connected_targets": [],
                        "indicator_only": False,
                    },
                    "suppressed_reason": f"{lock_reason}_retry_scheduled",
                    "retry": {
                        "kind": state.retry_kind,
                        "attempt": state.retry_attempt,
                        "delay_seconds": delay_seconds,
                        "next_retry_at": datetime.fromtimestamp(state.next_retry_at, timezone.utc).isoformat(),
                        "reason": state.retry_reason,
                    },
                }
                try:
                    _persist_heartbeat_state(state_path, state)
                except Exception:
                    pass
            return

        wake_reason = None
        if not retry_due and retry_pending and not wake_requested:
            return

        if wake_requested:
            wake_reason = self._consume_wake_request(session.session_id)
        elif retry_due:
            wake_reason = state.retry_reason or state.retry_kind
        else:
            if not scheduled_due:
                if wake_next:
                    return
                return
            if wake_next:
                wake_reason = self._consume_wake_request(session.session_id) or queued_wake_reason

        if not within_active_hours:
            return

        if retry_due:
            self._clear_retry_state(state)

        # If delivery is explicit and no targets are currently connected,
        # skip the heartbeat run to avoid burning cycles before a client attaches.
        if not wake_requested and not wake_next and not retry_due and delivery.mode == "explicit":
            delivery_targets = []
            for target in delivery.explicit_session_ids:
                if target.upper() == "CURRENT":
                    delivery_targets.append(session.session_id)
                else:
                    delivery_targets.append(target)
            connected_targets = [
                target for target in delivery_targets
                if target in self.connection_manager.session_connections
            ]
            if not connected_targets:
                # Heartbeats do not backfill: consume this window even if no
                # explicit targets are connected, so we don't "catch up" as
                # soon as a client attaches.
                state.last_run = now
                state.last_summary = {
                    "timestamp": datetime.now().isoformat(),
                    "ok_only": True,
                    "text": None,
                    "token": None,
                    "sent": False,
                    "artifacts": {"writes": [], "work_products": [], "bash_commands": []},
                    "delivery": {
                        "mode": delivery.mode,
                        "targets": delivery_targets,
                        "connected_targets": [],
                        "indicator_only": False,
                    },
                    "suppressed_reason": "no_connected_targets",
                }
                try:
                    _persist_heartbeat_state(state_path, state)
                except Exception:
                    pass
                return

        # Check HEARTBEAT.md (optional)
        hb_file = workspace / HEARTBEAT_FILE
        prefer_global_heartbeat = _session_prefers_global_heartbeat(session)

        # Seed or refresh HEARTBEAT.md from global memory for managed heartbeat
        # sessions so operational instruction changes propagate immediately.
        if GLOBAL_HEARTBEAT_PATH.exists():
            try:
                if prefer_global_heartbeat:
                    changed = _sync_heartbeat_file(hb_file, GLOBAL_HEARTBEAT_PATH)
                    if changed:
                        logger.info(
                            "Refreshed %s from global memory for session %s",
                            HEARTBEAT_FILE,
                            session.session_id,
                        )
                elif not hb_file.exists():
                    shutil.copy(GLOBAL_HEARTBEAT_PATH, hb_file)
                    logger.info("Seeded %s from global memory for session %s", HEARTBEAT_FILE, session.session_id)
            except Exception as e:
                logger.warning("Failed to prepare HEARTBEAT.md for %s: %s", session.session_id, e)

        # Some agent/tooling conventions look for memory files under
        # <workspace>/memory/. Seed there too (without overwriting) so heartbeat
        # runs don't fail if the model chooses that path.
        try:
            mem_dir = workspace / "memory"
            mem_dir.mkdir(exist_ok=True)
            mem_hb_file = mem_dir / HEARTBEAT_FILE
            if prefer_global_heartbeat and GLOBAL_HEARTBEAT_PATH.exists():
                _sync_heartbeat_file(mem_hb_file, GLOBAL_HEARTBEAT_PATH)
            elif not mem_hb_file.exists():
                if hb_file.exists():
                    shutil.copy(hb_file, mem_hb_file)
                elif GLOBAL_HEARTBEAT_PATH.exists():
                    shutil.copy(GLOBAL_HEARTBEAT_PATH, mem_hb_file)
        except Exception as e:
            logger.debug("Failed to seed memory/HEARTBEAT.md for %s: %s", session.session_id, e)

        heartbeat_content = ""
        if hb_file.exists():
            heartbeat_content = hb_file.read_text()
            # Filter sections by factory role scope (HQ vs local desktop)
            from universal_agent.heartbeat_scope_filter import filter_heartbeat_by_scope
            heartbeat_content = filter_heartbeat_by_scope(heartbeat_content, self.heartbeat_scope)
            if _is_effectively_empty(heartbeat_content):
                state.last_run = now
                state.last_summary = {
                    "timestamp": datetime.now().isoformat(),
                    "ok_only": True,
                    "text": "Heartbeat skipped: empty HEARTBEAT.md content.",
                    "token": None,
                    "sent": False,
                    "artifacts": {"writes": [], "work_products": [], "bash_commands": []},
                    "delivery": {
                        "mode": delivery.mode,
                        "targets": [],
                        "connected_targets": [],
                        "indicator_only": False,
                    },
                    "suppressed_reason": "empty_content",
                }
                _persist_heartbeat_state(state_path, state)
                return
        else:
            mem_hb_file = workspace / "memory" / HEARTBEAT_FILE
            if mem_hb_file.exists():
                heartbeat_content = mem_hb_file.read_text()
                if _is_effectively_empty(heartbeat_content):
                    state.last_run = now
                    state.last_summary = {
                        "timestamp": datetime.now().isoformat(),
                        "ok_only": True,
                        "text": "Heartbeat skipped: empty HEARTBEAT.md content.",
                        "token": None,
                        "sent": False,
                        "artifacts": {"writes": [], "work_products": [], "bash_commands": []},
                        "delivery": {
                            "mode": delivery.mode,
                            "targets": [],
                            "connected_targets": [],
                            "indicator_only": False,
                        },
                        "suppressed_reason": "empty_content",
                    }
                    _persist_heartbeat_state(state_path, state)
                    return
        if not heartbeat_content and schedule.require_file:
            return

        logger.info(
            "💓 Triggering heartbeat for %s%s",
            session.session_id,
            f" (wake={wake_reason})" if wake_requested and wake_reason else "",
        )
        await self._run_heartbeat(
            session,
            state,
            state_path,
            heartbeat_content,
            schedule,
            delivery,
            visibility,
            interval_source=interval_source,
            trigger_reason=wake_reason or ("retry_due" if retry_due else "scheduled"),
        )

    def _session_heartbeat_lock_reason(self, session: GatewaySession, now_ts: float) -> Optional[str]:
        if session.session_id in self.busy_sessions:
            return "heartbeat_busy"

        runtime = session.metadata.get("runtime", {})
        if not isinstance(runtime, dict):
            runtime = {}
        active_foreground_runs = int(_coerce_int(runtime.get("active_foreground_runs"), 0) or 0)
        if active_foreground_runs > 0:
            return "foreground_run_active"

        # Backstop check in case runtime metadata is stale.
        if self.connection_manager and hasattr(self.connection_manager, "session_connections"):
            active_connections = len(self.connection_manager.session_connections.get(session.session_id, set()))
            if active_connections > 0:
                return "foreground_connection_active"

        cooldown_seconds = max(0, int(self.foreground_cooldown_seconds))
        if cooldown_seconds <= 0:
            return None

        ts_candidates = [
            runtime.get("last_foreground_run_finished_at"),
            runtime.get("last_foreground_run_started_at"),
        ]
        for candidate in ts_candidates:
            parsed = _parse_iso_to_unix(candidate)
            if parsed is None:
                continue
            if (now_ts - parsed) < cooldown_seconds:
                return "foreground_cooldown_active"
            break
        return None

    async def _run_heartbeat(
        self,
        session: GatewaySession,
        state: HeartbeatState,
        state_path: Path,
        heartbeat_content: str,
        schedule: HeartbeatScheduleConfig,
        delivery: HeartbeatDeliveryConfig,
        visibility: HeartbeatVisibilityConfig,
        interval_source: str = "default",
        trigger_reason: str = "scheduled",
    ):
        """Execute the heartbeat using the gateway engine."""
        self.busy_sessions.add(session.session_id)
        keep_busy_until_collect_finishes = False
        timed_out = False
        run_failed = False
        should_schedule_continuation = False
        continuation_reason: Optional[str] = None
        task_hub_agent_id = f"heartbeat:{session.session_id}"
        task_hub_workflow_run_id = (
            str(session.metadata.get("run_id") or session.metadata.get("workflow_run_id") or "").strip()
            if isinstance(session.metadata, dict)
            else ""
        )
        task_hub_workflow_attempt_id = (
            str(session.metadata.get("attempt_id") or session.metadata.get("workflow_attempt_id") or "").strip()
            if isinstance(session.metadata, dict)
            else ""
        )
        task_hub_claimed: list[dict] = []
        task_hub_finalize_result: dict[str, int] = {
            "finalized": 0,
            "reopened": 0,
            "reviewed": 0,
            "completed": 0,
            "retry_exhausted": 0,
        }
        task_hub_finalize_state = "completed"
        task_hub_finalize_summary = "heartbeat_run_finished"
        task_hub_claimed_count = 0
        completed_event_payload: Optional[dict[str, Any]] = None
        
        # Resolve wake_reason for tracing
        _wake_reason = trigger_reason or "scheduled"
        
        # Create parent Logfire span for the entire heartbeat execution
        _hb_span = None
        run_started_at = datetime.now().isoformat()
        self._emit_event(
            {
                "type": "heartbeat_started",
                "session_id": session.session_id,
                "timestamp": run_started_at,
                "wake_reason": _wake_reason,
            }
        )
        if _LOGFIRE_AVAILABLE and logfire:
            _hb_span = logfire.span(
                "heartbeat_run",
                session_id=session.session_id,
                run_source="heartbeat",
                wake_reason=_wake_reason,
            )
            _hb_span.__enter__()
        
        def _mock_heartbeat_response(content: str) -> str:
            # Deterministic response for tests/CI (no external calls).
            ok_tokens_sorted = sorted(schedule.ok_tokens, key=len, reverse=True)
            for token in ok_tokens_sorted + ["ALERT_TEST_A", "ALERT_TEST_B"]:
                if token in content:
                    return token
            match = re.search(r"'([^']+)'", content)
            if match:
                return match.group(1)
            return schedule.ok_tokens[0] if schedule.ok_tokens else DEFAULT_OK_TOKENS[0]

        try:
            async def _broadcast_wire(event_type: str, data: dict) -> None:
                try:
                    payload = {
                        "type": event_type,
                        "data": data,
                        "timestamp": datetime.now().isoformat(),
                        "time_offset": data.get("time_offset") if isinstance(data, dict) else None,
                    }
                    await self.connection_manager.broadcast(session.session_id, payload)
                except Exception:
                    # Heartbeat should not fail due to UI broadcast issues.
                    pass

            # Drain pending system events for this session
            system_events: list[dict] = []
            if self.system_event_provider:
                system_events = self.system_event_provider(session.session_id)
            
            # Check if any event indicates an exec/cron completion
            has_exec_completion = any(
                ("exec" in str(evt).lower() and "finish" in str(evt).lower()) or
                ("cron" in str(evt).lower() and "complete" in str(evt).lower())
                for evt in system_events
            )
            
            # Build metadata with system events
            heartbeat_investigation_only = _resolve_heartbeat_investigation_only(default=False)
            metadata: dict = {
                "source": "heartbeat",
                "heartbeat_investigation_only": heartbeat_investigation_only,
                "heartbeat_effective_interval_seconds": int(schedule.every_seconds),
                "heartbeat_interval_source": str(interval_source or "default"),
            }
            if system_events:
                metadata["system_events"] = system_events
                logger.info("Injecting %d system events into heartbeat for %s", len(system_events), session.session_id)

            dispatch_actionable_count: Optional[int] = None
            dispatch_claimed_count: Optional[int] = None
            guard_policy = _heartbeat_guard_policy(
                actionable_count=None,
                brainstorm_candidate_count=0,
                system_event_count=len(system_events),
                has_exec_completion=has_exec_completion,
                has_heartbeat_content=bool(heartbeat_content.strip()),
            )
            max_proactive_per_cycle = int(guard_policy.get("max_proactive_per_cycle") or 1)
            max_system_events = int(guard_policy.get("max_system_events") or 1)

            # Proactive advisor variables (set inside Task Hub block, used after)
            _brainstorm_ctx_text = ""
            _pending_q_count = 0
            _morning_text = ""

            # Deterministic Task Hub pre-step: heartbeat consumes prepared dispatch queue.
            try:
                conn = connect_runtime_db(get_activity_db_path())
                conn.row_factory = sqlite3.Row  # type: ignore[name-defined]
                try:
                    task_hub.ensure_schema(conn)
                    stale_result = task_hub.release_stale_assignments(
                        conn,
                        agent_id_prefix="heartbeat:",
                        stale_after_seconds=max(
                            60,
                            _parse_int(os.getenv("UA_TASK_HUB_STALE_ASSIGNMENT_SECONDS"), 1800),
                        ),
                    )
                    if int(stale_result.get("finalized") or 0) > 0:
                        logger.warning(
                            "Released stale Task Hub heartbeat assignments: finalized=%s reopened=%s",
                            stale_result.get("finalized"),
                            stale_result.get("reopened"),
                        )

                    import time
                    if getattr(task_hub, "_last_pruned_timestamp", 0) < time.time() - 86400:
                        try:
                            _prune_res = task_hub.prune_settled_tasks(conn, retention_days=21)
                            if (_prune_res.get("items") or 0) > 0:
                                logger.info("Periodic background prune completed: %s", _prune_res)
                            task_hub._last_pruned_timestamp = time.time()
                        except Exception as _prune_err:
                            logger.error("Task Hub background pruning failed: %s", _prune_err)

                    queue = task_hub.get_dispatch_queue(conn, limit=max(3, max_proactive_per_cycle * 4))
                    dispatch_actionable_count = int(queue.get("eligible_total") or 0)

                    # ── Capacity Governor gate ──────────────────────────
                    # Check system-level capacity before claiming tasks.
                    # If the provider is under 429 backoff or all slots are
                    # full, skip dispatch and defer to the next cycle.
                    _capacity_ok = True
                    _capacity_reason = "not_checked"
                    try:
                        from universal_agent.services.capacity_governor import CapacityGovernor
                        _governor = CapacityGovernor.get_instance()
                        _capacity_ok, _capacity_reason = _governor.can_dispatch()
                        if not _capacity_ok:
                            logger.info(
                                "Capacity governor blocked dispatch for %s: %s",
                                session.session_id,
                                _capacity_reason,
                            )
                            if "api_down" in _capacity_reason:
                                await _broadcast_wire(
                                    "system_alert",
                                    {"message": f"CRITICAL INFERENCE DROP: {_capacity_reason}"}
                                )
                    except Exception as _cap_exc:
                        logger.debug("Capacity governor unavailable: %s", _cap_exc)
                    # ────────────────────────────────────────────────────

                    # Dispatch logic moved to todo_dispatch_service
                    task_hub_claimed = []
                    dispatch_claimed_count = 0
                    task_hub_claimed_count = dispatch_claimed_count
                    should_schedule_continuation = dispatch_claimed_count > 0
                    if should_schedule_continuation:
                        continuation_reason = "task_hub_followup"

                    # Enhancement 1: Escalation Pre-Check — enrich each claimed task
                    # with past escalation resolutions so the agent doesn't repeat mistakes.

                    if task_hub_claimed:
                        hub_event = {
                            "type": "task_hub_dispatch",
                            "payload": {
                                "queue_build_id": str(queue.get("queue_build_id") or ""),
                                "eligible_total": int(queue.get("eligible_total") or 0),
                                "claimed_count": len(task_hub_claimed),
                                "claimed": task_hub_claimed,
                                "workflow_run_id": task_hub_workflow_run_id or None,
                                "workflow_attempt_id": task_hub_workflow_attempt_id or None,
                            },
                            "created_at": datetime.now().isoformat(),
                            "session_id": session.session_id,
                        }
                        system_events.append(hub_event)
                        metadata["system_events"] = system_events
                        metadata["task_hub_dispatch"] = hub_event["payload"]
                        logger.info(
                            "Injected Task Hub dispatch payload (%d claimed / %d eligible) into heartbeat for %s",
                            len(task_hub_claimed),
                            int(queue.get("eligible_total") or 0),
                            session.session_id,
                        )

                        # Enhancement 3: Context Injection — search memory for relevant
                        # past work on claimed tasks and inject snippets into metadata.
                        try:
                            from universal_agent.memory.orchestrator import get_memory_orchestrator

                            broker = get_memory_orchestrator()
                            memory_context_snippets = []
                            for claimed in task_hub_claimed:
                                title = str(claimed.get("title") or "").strip()
                                if title:
                                    hits = broker.search(query=title, limit=2, direct_context=True)
                                    for hit in hits:
                                        snippet = hit.get("snippet") or hit.get("summary", "")
                                        if snippet:
                                            memory_context_snippets.append({
                                                "task_title": title,
                                                "snippet": snippet[:500],
                                                "source": hit.get("source", ""),
                                            })
                            if memory_context_snippets:
                                metadata["memory_context_for_tasks"] = memory_context_snippets
                                logger.info(
                                    "Injected %d memory context snippets for %d claimed tasks in %s",
                                    len(memory_context_snippets),
                                    len(task_hub_claimed),
                                    session.session_id,
                                )
                        except Exception:
                            pass  # memory context is advisory, never block heartbeat

                    # Phase 5: Proactive Advisor — brainstorm context + morning report
                    # Skip entirely in task-focused mode (task_hub_claimed > 0) —
                    # the agent won't see this data anyway, so don't waste DB queries.
                    if not task_hub_claimed:
                        try:
                            from universal_agent.services.proactive_advisor import (
                                build_brainstorm_context,
                                format_brainstorm_context_prompt,
                                build_morning_report,
                            )
                            _brainstorm_ctx = build_brainstorm_context(conn)
                            _brainstorm_ctx_text = format_brainstorm_context_prompt(_brainstorm_ctx)
                            _pending_q_count = len(task_hub.list_pending_questions(conn, limit=100))

                            # Morning report: trigger on first tick of the day
                            _morning_text = ""
                            last_run_ts = getattr(state, "last_run", None)
                            now_date = datetime.now().date()
                            last_date = None
                            if last_run_ts:
                                try:
                                    last_date = datetime.fromtimestamp(last_run_ts).date()
                                except Exception:
                                    pass
                            if last_date is None or last_date < now_date:
                                report = build_morning_report(conn)
                                _raw_morning_text = str(report.get("report_text") or "")
                                if _raw_morning_text:
                                    logger.info(
                                        "Morning report generated for %s (%d active, %d brainstorm)",
                                        session.session_id,
                                        report.get("total_active", 0),
                                        len(report.get("brainstorm_tasks") or []),
                                    )
                                    from universal_agent.services.health_evaluator import evaluate_health_snapshot
                                    try:
                                        eval_result = await evaluate_health_snapshot(report)
                                    except Exception as e:
                                        logger.error(f"Failed to evaluate health snapshot: {e}")
                                        eval_result = {}
                                    
                                    # Get capacity info
                                    max_coder = os.getenv("UA_MAX_CONCURRENT_VP_CODER", "1")
                                    max_general = os.getenv("UA_MAX_CONCURRENT_VP_GENERAL", "2")
                                    
                                    # Fetch active missions
                                    active_missions = []
                                    try:
                                        rows = conn.execute("SELECT task_id, title FROM task_hub_items WHERE status = 'delegated'").fetchall()
                                        for r in rows:
                                            tid = r["task_id"] if hasattr(r, "keys") else r[0]
                                            ttitle = r["title"] if hasattr(r, "keys") else r[1]
                                            active_missions.append(f"[{tid}] {ttitle}")
                                    except Exception as e:
                                        logger.debug("Failed to list active missions for capacity report: %s", e)
                                        
                                    _cap_report = "== CAPACITY REPORT ==\n"
                                    _cap_report += f"Max Concurrent VP Coder: {max_coder}\n"
                                    _cap_report += f"Max Concurrent VP General: {max_general}\n"
                                    _cap_report += f"Active VP Missions ({len(active_missions)}):\n"
                                    for m in active_missions:
                                        _cap_report += f"- {m}\n"
                                        
                                    dirs = eval_result.get("simone_directives", [])
                                    esc = eval_result.get("human_escalations", [])
                                    
                                    _morning_text = _cap_report + "\n"
                                    if dirs or esc:
                                        _morning_text += "== HEALTH CHECK DIRECTIVES ==\n"
                                        for d in dirs:
                                            _morning_text += f"- {d}\n"
                                        if esc:
                                            _morning_text += "\n== ESCALATIONS ==\n"
                                            for e in esc:
                                                _morning_text += f"- {e}\n"
                                    else:
                                        _morning_text += "== HEALTH CHECK ==\nAll systems nominal. No stuck tasks."

                            metadata["proactive_advisor"] = {
                                "brainstorm_task_count": len(_brainstorm_ctx),
                                "pending_question_count": _pending_q_count,
                                "morning_report": bool(_morning_text),
                            }
                        except Exception as pa_exc:
                            logger.debug("Proactive advisor unavailable: %s", pa_exc)
                            _brainstorm_ctx_text = ""
                            _pending_q_count = 0
                            _morning_text = ""
                    else:
                        logger.info(
                            "Skipping proactive advisor for %s (task-focused mode, %d tasks claimed)",
                            session.session_id,
                            len(task_hub_claimed),
                        )

                finally:
                    conn.close()
            except Exception as exc:
                logger.info("Task Hub heartbeat pre-step unavailable for %s: %s", session.session_id, exc)

            if len(system_events) > max_system_events:
                system_events = system_events[-max_system_events:]
                metadata["system_events"] = system_events

            guard_policy = _heartbeat_guard_policy(
                actionable_count=int(dispatch_actionable_count or 0),
                brainstorm_candidate_count=int(dispatch_claimed_count or 0),
                system_event_count=len(system_events),
                has_exec_completion=has_exec_completion,
                has_heartbeat_content=bool(heartbeat_content.strip()),
                pending_question_count=_pending_q_count,
            )
            guard_skip_reason = str(guard_policy.get("skip_reason") or "").strip()
            _is_reflection_mode = bool(guard_policy.get("reflection_mode", False))
            metadata["heartbeat_guard"] = {
                "autonomous_enabled": bool(guard_policy.get("autonomous_enabled")),
                "max_actionable": int(guard_policy.get("max_actionable") or DEFAULT_HEARTBEAT_MAX_ACTIONABLE),
                "max_system_events": int(guard_policy.get("max_system_events") or DEFAULT_HEARTBEAT_MAX_SYSTEM_EVENTS),
                "max_proactive_per_cycle": int(
                    guard_policy.get("max_proactive_per_cycle") or DEFAULT_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE
                ),
                "actionable_count": int(dispatch_actionable_count or 0),
                "brainstorm_candidate_count": int(dispatch_claimed_count or 0),
                "system_event_count": len(system_events),
                "skip_reason": guard_skip_reason or None,
                "reflection_mode": _is_reflection_mode,
            }

            # Phase 1: Reflection context injection — when the queue is empty
            # but we're in overnight reflection mode, build the reflection
            # prompt so the agent has goals/missions/context to work from.
            _reflection_ctx_text = ""
            if _is_reflection_mode:
                try:
                    from universal_agent.services.reflection_engine import (
                        build_reflection_context,
                        has_nightly_budget,
                        _increment_nightly_task_count,
                    )
                    ref_conn = connect_runtime_db(get_activity_db_path())
                    ref_conn.row_factory = sqlite3.Row  # type: ignore[name-defined]
                    try:
                        if has_nightly_budget(ref_conn):
                            ref_ctx = build_reflection_context(
                                ref_conn,
                                workspace_dir=str(session.workspace_dir),
                            )
                            _reflection_ctx_text = str(ref_ctx.get("reflection_prompt_text") or "")
                            _increment_nightly_task_count(ref_conn, increment=1)
                            metadata["reflection"] = {
                                "mode": True,
                                "nightly_task_count": ref_ctx.get("nightly_task_count", 0),
                                "budget_remaining": ref_ctx.get("nightly_budget_remaining", 0),
                                "stalled_brainstorms": len(ref_ctx.get("stalled_brainstorms") or []),
                                "recent_completions": len(ref_ctx.get("recent_completions") or []),
                            }
                            logger.info(
                                "Reflection context built for %s: budget_remaining=%d, stalled=%d",
                                session.session_id,
                                ref_ctx.get("nightly_budget_remaining", 0),
                                len(ref_ctx.get("stalled_brainstorms") or []),
                            )
                        else:
                            guard_skip_reason = "nightly_budget_exhausted"
                            _is_reflection_mode = False
                            logger.info(
                                "Reflection mode skipped for %s: nightly budget exhausted",
                                session.session_id,
                            )
                    finally:
                        ref_conn.close()
                except Exception as ref_exc:
                    logger.debug("Reflection engine unavailable: %s", ref_exc)
                    _reflection_ctx_text = ""

            # Phase 2: Morning report — on each heartbeat tick, check if the
            # 7 AM morning report email is due and fire-and-forget the send.
            # Access _agentmail_service from gateway_server module (lazy import
            # to avoid circular dependency at module load time).
            try:
                from universal_agent.services.morning_report_sender import (
                    MorningReportSender,
                )
                import universal_agent.gateway_server as _gw_mod
                _mr_agentmail = getattr(_gw_mod, "_agentmail_service", None)
                _mr_sender = MorningReportSender(
                    agentmail_service=_mr_agentmail,
                    task_hub_db_path="",
                )
                _mr_result = await _mr_sender.send_if_due()
                if _mr_result.get("sent"):
                    logger.info(
                        "☀️ Morning report sent during heartbeat tick: %s",
                        _mr_result.get("recipient"),
                    )
                    metadata["morning_report_sent"] = True
            except Exception as _mr_exc:
                logger.debug("Morning report check skipped: %s", _mr_exc)

            # Compose heartbeat prompt only after Task Hub claims are known so the
            # model can explicitly disposition claimed items before completion.
            #
            # Task-focused mode: when tasks are claimed from the dispatch queue,
            # switch to a lean prompt that skips all system monitoring.
            _is_task_focused = False
            
            if has_exec_completion:
                base_prompt = EXEC_EVENT_PROMPT
                logger.info("Using EXEC_EVENT_PROMPT for session %s (exec completion detected)", session.session_id)
            else:
                base_prompt = schedule.prompt.strip() or DEFAULT_HEARTBEAT_PROMPT
                if "{ok_token}" in base_prompt:
                    ok_token = schedule.ok_tokens[0] if schedule.ok_tokens else DEFAULT_OK_TOKENS[0]
                    base_prompt = base_prompt.replace("{ok_token}", ok_token)
            # Get runtime DB connection for VP review prompt injection
            _hb_runtime_conn = None
            try:
                _hb_runtime_conn = getattr(self.gateway, 'get_db_conn', lambda: None)()
            except Exception:
                pass
                
            _recent_topics_text = ""
            try:
                from universal_agent.services.proactive_topic_tracker import format_recent_topics_prompt
                _recent_topics_text = format_recent_topics_prompt(state) if not task_hub_claimed else ""
            except Exception as _trk_exc:
                logger.debug("Failed to format proactive topic history: %s", _trk_exc)

            prompt = _compose_heartbeat_prompt(
                base_prompt,
                investigation_only=heartbeat_investigation_only,
                task_hub_claims=task_hub_claimed,
                workspace_dir=str(session.workspace_dir),
                brainstorm_context_text=_brainstorm_ctx_text,
                morning_report_text=_morning_text,
                recent_topics_text=_recent_topics_text,
                runtime_conn=_hb_runtime_conn,
                task_focused=_is_task_focused,
            )
            # Append reflection context after other prompt sections (skip in task-focused mode)
            if _reflection_ctx_text and not _is_task_focused:
                prompt = f"{prompt}\n\n{_reflection_ctx_text}"
            
            full_response = ""
            streamed_chunks: list[str] = []
            final_text: Optional[str] = None
            saw_streaming_text = False

            # Enforce deterministic guard policy before expensive agent execution.
            should_skip_agent_run = bool(guard_skip_reason)

            # Track artifacts/commands for UI + last_summary
            write_paths: list[str] = []
            bash_commands: list[str] = []
            work_product_paths: list[str] = []

            # UI: mark background activity as "processing" when a client is attached.
            await _broadcast_wire(
                "status",
                {"status": "processing", "source": "heartbeat"},
            )
            await _broadcast_wire(
                "status",
                {
                    "status": "Heartbeat started",
                    "is_log": True,
                    "level": "INFO",
                    "prefix": "HEARTBEAT",
                    "source": "heartbeat",
                },
            )

            if should_skip_agent_run:
                full_response = schedule.ok_tokens[0] if schedule.ok_tokens else DEFAULT_OK_TOKENS[0]
                logger.info(
                    "Skipping heartbeat agent execution for %s (no actionable task-hub dispatch items)",
                    session.session_id,
                )
            elif os.getenv("UA_HEARTBEAT_MOCK_RESPONSE", "0").lower() in {"1", "true", "yes"}:
                full_response = _mock_heartbeat_response(heartbeat_content)
                logger.info("Heartbeat mock response enabled for %s", session.session_id)
            else:
                request = GatewayRequest(
                    user_input=prompt,
                    force_complex=True,  # Heartbeat always needs tools — skip classification
                    metadata=metadata,
                )

                async def _collect_events() -> None:
                    nonlocal full_response
                    nonlocal saw_streaming_text, final_text
                    async for event in self.gateway.execute(session, request):
                        if timed_out:
                            return
                        # Broadcast agent events into the session stream so connected UIs
                        # can see heartbeat activity in real time.
                        try:
                            # Avoid duplicating the final, aggregated response_text when streaming chunks exist.
                            if event.type == EventType.TEXT and isinstance(event.data, dict):
                                text = event.data.get("text", "") or ""
                                has_offset = "time_offset" in event.data
                                if has_offset:
                                    saw_streaming_text = True
                                    streamed_chunks.append(text)
                                else:
                                    final_text = text
                                    if saw_streaming_text:
                                        # Skip broadcasting the replay text (UI already has streamed chunks).
                                        continue

                            elif event.type == EventType.TEXT and isinstance(event.data, str):
                                final_text = event.data
                                if saw_streaming_text:
                                    continue

                            if event.type == EventType.TOOL_CALL and isinstance(event.data, dict):
                                tool_name = str(event.data.get("name") or "")
                                tool_input = event.data.get("input") if isinstance(event.data.get("input"), dict) else {}
                                if tool_name == "Write":
                                    fp = tool_input.get("file_path")
                                    if isinstance(fp, str) and fp:
                                        write_paths.append(fp)
                                if tool_name == "Bash":
                                    cmd = tool_input.get("command")
                                    if isinstance(cmd, str) and cmd:
                                        bash_commands.append(cmd)

                            if event.type == EventType.WORK_PRODUCT and isinstance(event.data, dict):
                                wp = event.data.get("path")
                                if isinstance(wp, str) and wp:
                                    work_product_paths.append(wp)

                            await _broadcast_wire(
                                event.type.value if hasattr(event.type, "value") else str(event.type),
                                event.data if isinstance(event.data, dict) else {"value": event.data},
                            )
                        except Exception:
                            pass

                        # Collect response text for OK-token stripping / suppression logic.
                        if event.type == EventType.TEXT:
                            if isinstance(event.data, dict):
                                full_response += event.data.get("text", "")
                            elif isinstance(event.data, str):
                                full_response += event.data

                collect_task = asyncio.create_task(_collect_events())
                try:
                    await asyncio.wait_for(collect_task, timeout=self.execution_timeout_seconds)
                except asyncio.TimeoutError:
                    timed_out = True
                    run_failed = True
                    task_hub_finalize_state = "failed"
                    task_hub_finalize_summary = f"heartbeat_timeout:{self.execution_timeout_seconds}s"
                    logger.error(
                        "Heartbeat execution timed out after %ss for %s",
                        self.execution_timeout_seconds,
                        session.session_id,
                    )
                    collect_task.cancel()
                    try:
                        await asyncio.wait_for(collect_task, timeout=5)
                    except asyncio.CancelledError:
                        pass
                    except asyncio.TimeoutError:
                        logger.error(
                            "Heartbeat collect task did not cancel within 5s for %s; "
                            "keeping session busy until it exits",
                            session.session_id,
                        )
                        keep_busy_until_collect_finishes = True
                        collect_task.add_done_callback(
                            lambda _: self.busy_sessions.discard(session.session_id)
                        )
                    full_response = "UA_HEARTBEAT_TIMEOUT"
                    await _broadcast_wire(
                        "status",
                        {
                            "status": f"Heartbeat timed out after {self.execution_timeout_seconds}s",
                            "is_log": True,
                            "level": "ERROR",
                            "prefix": "HEARTBEAT",
                            "source": "heartbeat",
                        },
                    )

            logger.info(f"Heartbeat response for {session.session_id}: '{full_response}'")

            # --- Phase 3 Logic ---
            # Prefer the non-streaming final text (when present) to avoid duplicated aggregation.
            # Skip overwrite when timed out — keep "UA_HEARTBEAT_TIMEOUT" as the canonical response.
            if not timed_out:
                if final_text is not None:
                    full_response = final_text
                elif streamed_chunks:
                    full_response = "".join(streamed_chunks)
            strip_result = _strip_heartbeat_tokens(
                full_response,
                schedule.ok_tokens,
                schedule.ack_max_chars,
            )
            ok_only = strip_result["ok_only"]
            response_text = strip_result["text"] or ""
            ok_token = strip_result["token"] or (schedule.ok_tokens[0] if schedule.ok_tokens else DEFAULT_OK_TOKENS[0])
            
            # Record proactive topics (only when proactive and non-OK)
            if not ok_only and response_text and not task_hub_claimed:
                try:
                    from universal_agent.services.proactive_topic_tracker import record_topic, extract_topic_fingerprint
                    _fp = extract_topic_fingerprint(response_text)
                    record_topic(state, topic_summary=response_text[:300], fingerprint=_fp)
                except Exception as _trk_exc:
                    logger.debug("Failed to record proactive topic history: %s", _trk_exc)
                    
            is_duplicate = False
            msg_hash = hashlib.sha256((response_text or full_response).encode()).hexdigest()
            now = time.time()
            
            # Policy 1: Visibility (showOk)
            suppress_ok = ok_only and not visibility.show_ok
            suppress_alerts = (not ok_only) and not visibility.show_alerts
            
            # Policy 2: Deduplication
            if not ok_only: # Only dedupe alerts, not OKs (OKs handled by showOk)
                if state.last_message_hash == msg_hash:
                    # Check window
                    if (now - state.last_message_ts) < visibility.dedupe_window_seconds:
                        is_duplicate = True
                        logger.info(f"Suppressed duplicate alert for {session.session_id} (hash={msg_hash[:8]})")
            
            # Policy 3: Delivery Mode
            should_send = True
            suppressed_reason: Optional[str] = None
            if delivery.mode == "none":
                should_send = False
                suppressed_reason = "delivery_none"
            elif suppress_ok:
                should_send = False
                suppressed_reason = "ok_suppressed"
                logger.info(f"Suppressed OK heartbeat for {session.session_id} (show_ok=False)")
            elif suppress_alerts:
                should_send = False
                suppressed_reason = "alerts_suppressed"
                logger.info(f"Suppressed alert heartbeat for {session.session_id} (show_alerts=False)")
            elif is_duplicate:
                should_send = False
                suppressed_reason = "dedupe"

            delivery_targets = []
            if delivery.mode == "last":
                delivery_targets = [session.session_id]
            elif delivery.mode == "explicit":
                for target in delivery.explicit_session_ids:
                    if target.upper() == "CURRENT":
                        delivery_targets.append(session.session_id)
                    else:
                        delivery_targets.append(target)

            if not delivery_targets:
                should_send = False
                suppressed_reason = suppressed_reason or "no_targets"

            connected_targets = [
                target for target in delivery_targets
                if target in self.connection_manager.session_connections
            ]
            if should_send and not connected_targets:
                should_send = False
                suppressed_reason = suppressed_reason or "no_connected_targets"

            # Allow indicator-only event when OK is suppressed but indicators are enabled.
            allow_indicator = ok_only and suppress_ok and visibility.use_indicator
            if allow_indicator and connected_targets:
                should_send = True
                suppressed_reason = None
            
            sent_any = False
            summary_text = ok_token if ok_only else (response_text or full_response)
            if should_send:
                if allow_indicator:
                    summary_event = {
                        "type": "system_event",
                        "data": {
                            "type": "heartbeat_indicator",
                            "payload": {
                                "timestamp": datetime.now().isoformat(),
                                "ok_only": True,
                                "delivered": {
                                    "mode": delivery.mode,
                                    "targets": delivery_targets,
                                },
                            },
                            "created_at": datetime.now().isoformat(),
                            "session_id": session.session_id,
                        },
                    }
                else:
                    summary_event = {
                        "type": "system_event",
                        "data": {
                            "type": "heartbeat_summary",
                            "payload": {
                                "text": summary_text,
                                "timestamp": datetime.now().isoformat(),
                                "ok_only": ok_only,
                                "delivered": {
                                    "mode": delivery.mode,
                                    "targets": delivery_targets,
                                    "is_duplicate": is_duplicate,
                                },
                            },
                            "created_at": datetime.now().isoformat(),
                            "session_id": session.session_id,
                        },
                    }

                for target_session_id in connected_targets:
                    await self.connection_manager.broadcast(target_session_id, summary_event)
                    sent_any = True
                
                # Update last message state only if sent (so we don't dedupe against something we never showed)
                # Actually, for dedupe, if we suppressed A because it was A, we keep the OLD timestamp (so window doesn't reset).
                # But if we sent it, we update.
                if sent_any and not ok_only:
                    state.last_message_hash = msg_hash
                    state.last_message_ts = now

            state.last_summary = {
                "timestamp": datetime.now().isoformat(),
                "ok_only": ok_only,
                "text": summary_text,
                "token": ok_token if ok_only else None,
                "sent": sent_any,
                "artifacts": {
                    "writes": write_paths[-50:],
                    "work_products": work_product_paths[-50:],
                    "bash_commands": bash_commands[-50:],
                },
                "delivery": {
                    "mode": delivery.mode,
                    "targets": delivery_targets,
                    "connected_targets": connected_targets,
                    "indicator_only": allow_indicator,
                },
                "suppressed_reason": suppressed_reason,
                "retry": {
                    "kind": state.retry_kind,
                    "attempt": state.retry_attempt,
                    "delay_seconds": state.last_retry_delay_seconds,
                    "next_retry_at": (
                        datetime.fromtimestamp(state.next_retry_at, timezone.utc).isoformat()
                        if state.next_retry_at > 0
                        else None
                    ),
                    "reason": state.retry_reason,
                },
            }

            # Emit Logfire classification marker after heartbeat completes
            if _LOGFIRE_AVAILABLE and logfire:
                if not ok_only:
                    logfire.info(
                        "heartbeat_significant",
                        session_id=session.session_id,
                        run_source="heartbeat",
                        tools_used=len(write_paths) + len(bash_commands),
                        artifacts_written=write_paths[-20:],
                        work_products=work_product_paths[-20:],
                        response_summary=(summary_text or "")[:500],
                    )
                else:
                    logfire.info(
                        "heartbeat_ok",
                        session_id=session.session_id,
                        run_source="heartbeat",
                    )

            await _broadcast_wire(
                "status",
                {
                    "status": "Heartbeat complete",
                    "is_log": True,
                    "level": "INFO",
                    "prefix": "HEARTBEAT",
                    "source": "heartbeat",
                },
            )
            await _broadcast_wire(
                "query_complete", {}
            )

            # Always update last_run to respect interval
            state.last_run = now
            if run_failed:
                self._schedule_retry(
                    state,
                    session_id=session.session_id,
                    now_ts=now,
                    kind="failure",
                    reason="heartbeat_timeout" if timed_out else "heartbeat_failed",
                )
            elif should_schedule_continuation:
                self._schedule_continuation_retry(
                    state,
                    now_ts=now,
                    reason=continuation_reason or "success_recheck",
                )
            else:
                self._clear_retry_state(state)

            state.last_summary["retry"] = {
                "kind": state.retry_kind,
                "attempt": state.retry_attempt,
                "delay_seconds": state.last_retry_delay_seconds,
                "next_retry_at": (
                    datetime.fromtimestamp(state.next_retry_at, timezone.utc).isoformat()
                    if state.next_retry_at > 0
                    else None
                ),
                "reason": state.retry_reason,
            }
            _persist_heartbeat_state(state_path, state)

            # ------------------------------------------------------------------
            # Synthetic findings: when the agent completes a non-OK run but did
            # not write heartbeat_findings_latest.json, create a minimal
            # synthetic one so the gateway can always parse structured findings.
            # ------------------------------------------------------------------
            _findings_filename = "heartbeat_findings_latest.json"
            _findings_written = any(
                _findings_filename in str(p)
                for p in (write_paths + work_product_paths)
            )

            # ── Task-focused mode: always write deterministic findings ────
            # In task-focused runs the agent is told NOT to write findings.
            # Python writes a task-run-aware record instead — zero LLM cost.
            if _is_task_focused:
                try:
                    _wp_dir = Path(session.workspace_dir) / "work_products"
                    _wp_dir.mkdir(parents=True, exist_ok=True)

                    _task_titles = [
                        str(c.get("title") or "untitled").strip()
                        for c in task_hub_claimed
                    ]
                    if run_failed:
                        _tf_status = "critical"
                        _tf_summary = (
                            f"Task run failed ({len(task_hub_claimed)} tasks claimed: "
                            f"{', '.join(_task_titles[:3])}). "
                            f"Response preview: {(response_text or full_response)[:200]}"
                        )
                    else:
                        _tf_status = "ok"
                        _tf_summary = (
                            f"Task run completed ({len(task_hub_claimed)} tasks: "
                            f"{', '.join(_task_titles[:3])})"
                        )

                    _task_findings = {
                        "version": 1,
                        "overall_status": _tf_status,
                        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                        "source": "task_run",
                        "summary": _tf_summary,
                        "task_run": {
                            "claimed_count": len(task_hub_claimed),
                            "task_titles": _task_titles[:5],
                            "run_failed": run_failed,
                            "timed_out": timed_out,
                        },
                        "findings": [],
                    }
                    _tf_path = _wp_dir / _findings_filename
                    _tf_path.write_text(
                        json.dumps(_task_findings, indent=2, default=str),
                        encoding="utf-8",
                    )
                    work_product_paths.append(str(_tf_path))
                    logger.info(
                        "Wrote task-run findings (%s, %d tasks) for %s → %s",
                        _tf_status,
                        len(task_hub_claimed),
                        session.session_id,
                        _tf_path,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to write task-run findings for %s: %s",
                        session.session_id,
                        exc,
                    )
            elif _findings_written:
                # ── Post-write validation: repair & re-serialize agent JSON ──
                try:
                    _wp_dir = Path(session.workspace_dir) / "work_products"
                    _agent_path = _wp_dir / _findings_filename
                    if _agent_path.exists():
                        _raw = _agent_path.read_text(encoding="utf-8")
                        _validated = extract_json_payload(_raw, model=HeartbeatFindings)
                        if isinstance(_validated, HeartbeatFindings):
                            _clean = _validated.model_dump()
                        elif isinstance(_validated, dict):
                            _clean = _validated
                        else:
                            _clean = None
                        if _clean is not None:
                            _agent_path.write_text(
                                json.dumps(_clean, indent=2, default=str),
                                encoding="utf-8",
                            )
                            logger.debug(
                                "Post-write validation repaired findings for %s",
                                session.session_id,
                            )
                except Exception as exc:
                    logger.warning(
                        "Post-write findings validation failed for %s: %s",
                        session.session_id,
                        exc,
                    )

            # ------------------------------------------------------------------
            # Always-write contract: every heartbeat run must produce a
            # structured findings JSON.  This ensures that the gateway can
            # always parse the result, and *absence* of the file reliably
            # signals a genuine failure.
            #
            #   ok_only  + not run_failed  →  "ok"       / "200 OK"
            #   not ok   + not run_failed  →  "ok"       / preview of response
            #   run_failed                 →  "critical"  / error details
            # ------------------------------------------------------------------
            if not _findings_written and not should_skip_agent_run and not _is_task_focused:
                try:
                    _wp_dir = Path(session.workspace_dir) / "work_products"
                    _wp_dir.mkdir(parents=True, exist_ok=True)

                    if run_failed:
                        _synth_status = "critical"
                        _synth_summary = (
                            f"Heartbeat run failed. Response preview: "
                            f"{(response_text or full_response)[:200]}"
                        )
                        _synth_findings = [
                            {
                                "finding_id": "synthetic_missing_findings_artifact",
                                "category": "gateway",
                                "severity": "critical",
                                "metric_key": "heartbeat_findings_artifact_written",
                                "observed_value": False,
                                "threshold_text": "agent should write findings JSON",
                                "known_rule_match": True,
                                "confidence": "medium",
                                "title": "Heartbeat Run Failed Without Structured Output",
                                "recommendation": (
                                    "Review heartbeat response text for details. "
                                    "The agent did not produce a structured findings "
                                    "JSON during this run."
                                ),
                                "runbook_command": "",
                                "metadata": {
                                    "ok_only": ok_only,
                                    "run_failed": run_failed,
                                    "timed_out": timed_out,
                                    "write_count": len(write_paths),
                                    "work_product_count": len(work_product_paths),
                                },
                            }
                        ]
                    else:
                        # Successful run — either ok_only or non-ok text output.
                        # Both are healthy; write an "ok" status so the gateway
                        # sees a clean bill of health.
                        _synth_status = "ok"
                        _synth_summary = "200 OK"
                        _synth_findings = []

                    _synthetic = {
                        "version": 1,
                        "overall_status": _synth_status,
                        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                        "source": "heartbeat_synthetic",
                        "summary": _synth_summary,
                        "findings": _synth_findings,
                    }
                    _synthetic_path = _wp_dir / _findings_filename
                    _synthetic_path.write_text(
                        json.dumps(_synthetic, indent=2, default=str),
                        encoding="utf-8",
                    )
                    work_product_paths.append(str(_synthetic_path))
                    logger.info(
                        "Wrote synthetic heartbeat findings (%s) for %s → %s",
                        _synth_status,
                        session.session_id,
                        _synthetic_path,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to write synthetic heartbeat findings for %s: %s",
                        session.session_id,
                        exc,
                    )

            completed_event_payload = {
                "type": "heartbeat_completed",
                "session_id": session.session_id,
                "timestamp": datetime.now().isoformat(),
                "ok_only": ok_only,
                "suppressed_reason": suppressed_reason,
                "sent": sent_any,
                "guard_reason": str((metadata.get("heartbeat_guard") or {}).get("skip_reason") or ""),
                "guard": metadata.get("heartbeat_guard") if isinstance(metadata.get("heartbeat_guard"), dict) else {},
                "heartbeat_interval_source": str(interval_source or "default"),
                "heartbeat_effective_interval_seconds": int(schedule.every_seconds),
                "artifacts": {
                    "writes": write_paths[-50:],
                    "work_products": work_product_paths[-50:],
                    "bash_commands": bash_commands[-50:],
                },
                "retry": {
                    "kind": state.retry_kind,
                    "attempt": state.retry_attempt,
                    "delay_seconds": state.last_retry_delay_seconds,
                    "next_retry_at": (
                        datetime.fromtimestamp(state.next_retry_at, timezone.utc).isoformat()
                        if state.next_retry_at > 0
                        else None
                    ),
                    "reason": state.retry_reason,
                },
            }

        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = "429" in error_str or "too many requests" in error_str or "overloaded" in error_str
            if is_rate_limit:
                from universal_agent.services.capacity_governor import CapacityGovernor
                asyncio.ensure_future(
                    CapacityGovernor.get_instance().report_rate_limit(
                        "heartbeat_simone", error=e
                    )
                )

            run_failed = True
            task_hub_finalize_state = "failed"
            task_hub_finalize_summary = f"heartbeat_failed:{str(e)[:180]}"
            logger.error(f"Heartbeat execution failed for {session.session_id}: {e}")
            self._emit_event(
                {
                    "type": "heartbeat_failed",
                    "session_id": session.session_id,
                    "timestamp": datetime.now().isoformat(),
                    "error": str(e),
                }
            )
            now_ts = time.time()
            state.last_run = now_ts
            self._schedule_retry(
                state,
                session_id=session.session_id,
                now_ts=now_ts,
                kind="failure",
                reason="heartbeat_failed",
            )
            try:
                _persist_heartbeat_state(state_path, state)
            except Exception:
                pass
        finally:
            if task_hub_claimed:
                assignment_ids = [
                    str(item.get("assignment_id") or "").strip()
                    for item in task_hub_claimed
                    if str(item.get("assignment_id") or "").strip()
                ]
                if assignment_ids:
                    conn = None
                    try:
                        conn = connect_runtime_db(get_activity_db_path())
                        conn.row_factory = sqlite3.Row  # type: ignore[name-defined]
                        heartbeat_retry_budget = max(
                            1,
                            _parse_int(os.getenv("UA_TASK_HUB_HEARTBEAT_MAX_RETRIES"), 3),
                        )
                        task_hub_finalize_result = task_hub.finalize_assignments(
                            conn,
                            assignment_ids=assignment_ids,
                            state=task_hub_finalize_state,
                            result_summary=task_hub_finalize_summary,
                            reopen_in_progress=True,
                            policy="heartbeat",
                            heartbeat_max_retries=heartbeat_retry_budget,
                        )
                        logger.info(
                            "Finalized Task Hub heartbeat claims for %s: state=%s finalized=%s completed=%s reviewed=%s reopened=%s retry_exhausted=%s%s",
                            session.session_id,
                            task_hub_finalize_state,
                            task_hub_finalize_result.get("finalized"),
                            task_hub_finalize_result.get("completed"),
                            task_hub_finalize_result.get("reviewed"),
                            task_hub_finalize_result.get("reopened"),
                            task_hub_finalize_result.get("retry_exhausted"),
                            " (run_failed)" if run_failed else "",
                        )
                        if int(task_hub_finalize_result.get("reviewed") or 0) > 0 or int(
                            task_hub_finalize_result.get("reopened") or 0
                        ) > 0:
                            logger.info(
                                "Task Hub heartbeat disposition for %s: moved_to_review=%s reopened=%s",
                                session.session_id,
                                int(task_hub_finalize_result.get("reviewed") or 0),
                                int(task_hub_finalize_result.get("reopened") or 0),
                            )
                    except Exception as exc:
                        logger.warning(
                            "Failed to finalize Task Hub heartbeat claims for %s: %s",
                            session.session_id,
                            exc,
                        )
                    finally:
                        if conn is not None:
                            conn.close()

                    # ── Phase 6: Completion Feedback Loop ─────────────────
                    # After successful finalization, if tasks were completed
                    # AND there are more eligible tasks in the queue, schedule
                    # a wake for the next heartbeat cycle. We no longer
                    # unconditionally wake — doing so created a tight race
                    # window where a just-completed task could be re-claimed
                    # before the SQLite commit fully propagated.
                    try:
                        completed_count = int(task_hub_finalize_result.get("completed") or 0)
                        if completed_count > 0 and not run_failed:
                            # Check if there are MORE eligible tasks waiting
                            _remaining_eligible = 0
                            _feedback_conn = None
                            try:
                                _feedback_conn = connect_runtime_db(get_activity_db_path())
                                _feedback_conn.row_factory = sqlite3.Row  # type: ignore[name-defined]
                                _q = task_hub.get_dispatch_queue(_feedback_conn, limit=3)
                                _remaining_eligible = int(_q.get("eligible_total") or 0)
                            except Exception:
                                pass
                            finally:
                                if _feedback_conn is not None:
                                    _feedback_conn.close()

                            if _remaining_eligible > 0:
                                self.request_heartbeat_next(
                                    session.session_id,
                                    reason=f"completion_feedback:{completed_count}_done,{_remaining_eligible}_remaining",
                                )
                                logger.info(
                                    "⚡ Completion feedback: wake-next for %s after %d task(s) completed (%d remaining)",
                                    session.session_id,
                                    completed_count,
                                    _remaining_eligible,
                                )
                            else:
                                logger.info(
                                    "⚡ Completion feedback: NO wake-next for %s (no remaining eligible tasks after %d completed)",
                                    session.session_id,
                                    completed_count,
                                )
                    except Exception as exc:
                        # Never let the feedback loop break finalization
                        logger.debug(
                            "Completion feedback wake failed (non-fatal): %s", exc,
                        )

            if completed_event_payload is not None:
                completed_event_payload.update(
                    {
                        "task_hub_claimed_count": int(task_hub_claimed_count),
                        "task_hub_completed_count": int(task_hub_finalize_result.get("completed") or 0),
                        "task_hub_review_count": int(task_hub_finalize_result.get("reviewed") or 0),
                        "task_hub_reopened_count": int(task_hub_finalize_result.get("reopened") or 0),
                        "task_hub_retry_exhausted_count": int(
                            task_hub_finalize_result.get("retry_exhausted") or 0
                        ),
                    }
                )
                self._emit_event(completed_event_payload)
            # Close the heartbeat Logfire span
            if _hb_span is not None:
                try:
                    _hb_span.__exit__(None, None, None)
                except Exception:
                    pass
            if not keep_busy_until_collect_finishes:
                self.busy_sessions.discard(session.session_id)

