import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import shlex
import shutil
import time
from typing import Any, Callable, Dict, Iterable, Optional
import uuid

from croniter import croniter
import pytz

from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import get_run_attempt
from universal_agent.gateway import GatewayRequest, InProcessGateway
from universal_agent.heartbeat_service import _parse_duration_seconds
from universal_agent.workflow_admission import WorkflowAdmissionService, WorkflowTrigger

logger = logging.getLogger(__name__)

MIN_CRON_TIMEOUT_SECONDS = 1
MAX_CRON_TIMEOUT_SECONDS = 7200
MIN_CRON_INTERVAL_SECONDS = 60
_CRON_DB_LOCK_RETRY_DELAY_SECONDS = 1.0
_CRON_DB_LOCK_RETRY_MAX = 5

# Deploy-window detection — `.github/workflows/deploy.yml` touches this
# file just before `systemctl restart` and removes it on EXIT (or after
# a 25-minute safety timer). When a cron subprocess is SIGTERM'd inside
# this window, the kill is a deploy-restart side effect, not a real
# failure — classify the run as cancelled (matches the asyncio
# CancelledError path) and advance next_run_at so the existing
# scheduler picks up the work on the next gateway boot. Without this,
# every deploy that overlaps a long cron generates an `[ERROR]
# Autonomous Task Failed` + `[WARNING] Retrying` email pair for what's
# actually a non-event.
_DEPLOY_WINDOW_FLAG_PATH = "/tmp/ua-deployment-window"
# Fallback window: if the flag file is missing (very old deploys, or
# the flag's cleanup ran before us), treat any signal kill within
# 60 seconds of gateway start as deploy-induced. Cheap, conservative.
_DEPLOY_WINDOW_FALLBACK_UPTIME_SEC = 60
# How far in the future to push next_run_at when a cron is cancelled
# by a deploy restart. Small positive offset (5s) so the scheduler's
# missed-window detection (cron_service.py:605) picks it up cleanly
# on next boot without thundering-herd risk if multiple crons were
# in flight.
_DEPLOY_CANCEL_BACKFILL_OFFSET_SEC = 5
# Gateway process-start cache. Populated lazily on first call; never
# re-read because the process can't restart without dropping the
# module from memory.
_PROCESS_START_TIME: Optional[float] = None


def _process_start_time() -> float:
    """Return this process's start time as epoch seconds (cached)."""
    global _PROCESS_START_TIME
    if _PROCESS_START_TIME is None:
        try:
            # Linux: /proc/self/stat field 22 is starttime in clock ticks
            # since boot. Convert with btime + ticks/sec.
            with open("/proc/self/stat", encoding="utf-8") as f:
                fields = f.read().split()
            starttime_ticks = float(fields[21])
            ticks_per_sec = float(os.sysconf("SC_CLK_TCK"))
            with open("/proc/stat", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("btime "):
                        btime = float(line.split()[1])
                        break
                else:
                    raise ValueError("btime not found in /proc/stat")
            _PROCESS_START_TIME = btime + (starttime_ticks / ticks_per_sec)
        except (OSError, ValueError, IndexError):
            # Fallback: use module-import time. Pessimistic but never wrong
            # in a way that suppresses real failures (a positive uptime
            # always grows; we only widen the deploy window, not narrow it).
            _PROCESS_START_TIME = time.time()
    return _PROCESS_START_TIME


# ── M4: selective cron→heartbeat coupling policy (shared) ──────────────────
# The cron→heartbeat coupling historically woke Simone's heartbeat on EVERY
# autonomous-cron success. That wake is now redundant with the live Python
# priority dispatcher + idle_dispatch_loop, which pick up dispatch-eligible
# work without a Simone turn. So the coupling is SELECTIVE (default-deny
# allowlist). These two helpers are the single source of truth for the policy
# and are consumed by BOTH coupling lanes:
#   - gateway_server.py::_maybe_wake_heartbeat_after_autonomous_cron (the
#     autonomous-cron coupling, the measured ~62/hr driver), and
#   - cron_service.py::CronService._maybe_wake_heartbeat (the session-bound
#     metadata.wake_heartbeat back door).
# They live here (the lower module) because gateway_server already imports
# cron_service at module level, whereas cron_service imports gateway_server only
# lazily — defining them here avoids both a circular import and policy drift.
def coupling_wake_selective_enabled() -> bool:
    """Master flag for the M4 selective coupling gate (default ON).

    Flip ``UA_CRON_HEARTBEAT_WAKE_SELECTIVE`` OFF (+ gateway restart) to revert
    to the pre-M4 "wake on every autonomous cron" behavior — the escape hatch
    for this hot-path change.
    """
    return str(os.getenv("UA_CRON_HEARTBEAT_WAKE_SELECTIVE", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def coupling_wake_allowed_jobs() -> frozenset[str]:
    """``system_job`` names whose completion legitimately needs a Simone
    heartbeat to ACT.

    Default EMPTY: dispatch is handled by the live Python dispatcher +
    idle_dispatch_loop; urgent work wakes Simone via ``request_heartbeat_now``
    (a different path). So no autonomous cron needs to wake her on completion
    today (audited 2026-06-15). The comma-separated env override
    ``UA_CRON_HEARTBEAT_WAKE_ALLOWLIST`` lets a future cron opt in WITHOUT a code
    change — but it then needs a gateway restart to load (Infisical → process
    env at start).
    """
    raw = (os.getenv("UA_CRON_HEARTBEAT_WAKE_ALLOWLIST") or "").strip()
    return frozenset(j.strip() for j in raw.split(",") if j.strip())


def coupling_wake_min_interval_seconds() -> int:
    """Debounce: minimum seconds between cron-coupled heartbeat wakes (default
    300; 0 disables).

    Canonical reader of ``UA_CRON_HEARTBEAT_WAKE_MIN_INTERVAL_SECONDS`` — shared by
    the gateway hot-path debounce (``_cron_wake_min_interval_seconds`` delegates
    here) and the ZAI Control read-out, so the live value and the displayed value
    can never drift.
    """
    try:
        return int(str(os.getenv("UA_CRON_HEARTBEAT_WAKE_MIN_INTERVAL_SECONDS", "300")).strip())
    except (TypeError, ValueError):
        return 300


def _parse_script_command_argv(raw_command: str) -> list[str]:
    """Split a ``!script <module> [args...]`` cron command into argv.

    Returns the list ready to pass after ``python -m``: the first element
    is the dotted module path (with ``path/to/file.py`` shorthand
    normalised to dots), followed by any positional/CLI arguments.

    Raises ``ValueError`` if the command does not start with ``!script `` or
    contains no module token.
    """
    body = raw_command.strip()
    if not body.startswith("!script "):
        raise ValueError(f"not a !script command: {raw_command!r}")
    body = body[len("!script "):].strip()
    tokens = shlex.split(body)
    if not tokens:
        raise ValueError(f"empty !script command: {raw_command!r}")
    module = tokens[0].replace("/", ".")
    if module.endswith(".py"):
        module = module[:-3]
    return [module, *tokens[1:]]


def _is_deploy_window_active() -> bool:
    """True iff a deploy is currently in flight or just completed.

    Two signals, OR'd:

    1. The deploy-marker flag file (``/tmp/ua-deployment-window``) is
       present. `.github/workflows/deploy.yml` creates this BEFORE the
       systemctl restart and removes it on EXIT. Primary signal.
    2. This gateway process started within the last 60 seconds. Fallback
       when the flag's cleanup ran before the cron's failure-handler
       (rare race), or when the flag wasn't set (manual ops restart that
       happens to look like a deploy).

    Note (1) covers all GitHub-Actions-driven deploys; (2) widens to
    cover the rare race + makes the system tolerant of operator-initiated
    `systemctl restart` for ops reasons. Both signals are conservative:
    they widen the "treat signal as deploy-cancellation" window, never
    narrow it. Real failures (OOM, code crash, etc.) outside this window
    surface loudly as before.
    """
    try:
        if os.path.exists(_DEPLOY_WINDOW_FLAG_PATH):
            return True
    except OSError:
        pass
    try:
        uptime = time.time() - _process_start_time()
        if 0 <= uptime <= _DEPLOY_WINDOW_FALLBACK_UPTIME_SEC:
            return True
    except Exception:  # noqa: BLE001 — never block cron flow
        pass
    return False


def _is_llm_deploy_kill_result(result: Any) -> bool:
    """True iff a gateway LLM result carries the deploy-kill signature.

    When a deploy restart SIGTERMs the SDK's `claude` CLI subprocess
    mid-run, the SDK logs "Fatal error in message reader: Command failed
    with exit code 143" internally and the message stream simply ends —
    no exception propagates into ``gateway.run_query``, which returns a
    ``GatewayResult`` with empty ``response_text``, zero ``tool_calls``,
    and no collected errors (the transcript shows "No tools called").
    Without detection, the Phase F.1 close computes ``_f_rc_equiv_llm=0``
    and mis-paints the kill as ``clean_exit_zero`` — the run is marked
    completed and the artifact notifier discloses stale artifacts as
    fresh (observed live 2026-06-09/10, paper_to_podcast).

    This predicate is the *signature* only; callers must AND it with
    ``_is_deploy_window_active()`` before downgrading — mirroring the
    `!script` branch's guardrail that the deploy-window predicate is the
    ONLY thing allowed to downgrade a failure-shaped outcome.
    """
    response_text = (getattr(result, "response_text", "") or "").strip()
    try:
        tool_calls = int(getattr(result, "tool_calls", 0) or 0)
    except (TypeError, ValueError):
        tool_calls = 0
    return not response_text and tool_calls == 0


_CRON_MEDIA_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".svg",
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
}
_CRON_WORK_PRODUCT_EXTENSIONS = {
    ".html",
    ".pdf",
    ".csv",
    ".json",
    ".md",
    ".txt",
    ".xlsx",
    ".xls",
    ".docx",
    ".pptx",
}
_CRON_WORKSPACE_KEEP_FILES = {
    "run.log",
    "transcript.md",
    "trace.json",
    "trace_catalog.md",
    "MEMORY.md",
}
_CRON_OUTPUT_FILENAME = "cron_result.md"


def _persist_cron_run_output(
    workspace_dir: str,
    job_id: str,
    run_id: str,
    exit_code: Optional[int],
    output_text: str,
) -> None:
    """Write full subprocess stdout+stderr to ``<workspace>/run.log``.

    The CronRunRecord ``output_preview`` field is capped at 400 chars, which
    truncates Python tracebacks at the call site and loses the actual
    exception. This writes the full captured text to the per-session
    ``run.log`` (overwrite mode) so the per-attempt artifact snapshot
    captures it into ``attempts/NNN/run.log`` at attempt finalize. Best-
    effort: any I/O failure is logged and swallowed.
    """
    try:
        path = Path(workspace_dir) / "run.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"=== cron run_id={run_id} job_id={job_id} "
            f"finished_at={datetime.now(timezone.utc).isoformat()} "
            f"exit_code={exit_code} ===\n"
        )
        path.write_text(header + (output_text or ""), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Failed to persist full cron output for job %s run %s: %s",
            job_id, run_id, exc,
        )


# ── Non-retryable cron-failure classifier ──────────────────────────────
#
# Some exceptions during cron tick execution should NOT trigger the
# default 3-attempt retry policy:
#   - HTTP 429 from any upstream we depend on: hammering the rate-limit
#     three times in quick succession is the opposite of what the
#     server is asking for. The natural cron schedule (next tick in
#     <= 60s) is the right backoff.
#
# Background — observed 2026-05-23 06:50-07:12 UTC: the
# atlas_direct_dispatch cron's heavyweight bootstrap hit Composio's
# Vercel-fronted edge with a per-minute POST and got HTTP 429. Each
# failing tick spawned 2 retries (= 3 attempts total), tripling the
# call rate during the rate-limit window. The fix is to recognise
# rate-limit signatures and let the cron's own schedule reissue at
# the next tick instead of immediately re-firing.
#
# Detection keys off the error text. The httpx 429 status doesn't
# survive into the captured exception body (the Composio SDK wraps
# the response body — Vercel's HTML interstitial — into the
# exception message and discards the status code). So we pattern-match
# on the body signatures that the gateway_server classifier already
# knows about; keeping the source of truth there makes it easy to
# update both alert-dedup and retry policy in lockstep.
_RATE_LIMIT_BODY_TOKENS: tuple[str, ...] = (
    "vercel security checkpoint",       # Composio edge 429 (2026-05-23)
    "vercel.link/security-checkpoint",  # canonical Vercel link
    "429 too many requests",            # raw httpx error string
    "rate limit exceeded",
    "too many requests",
)


def _is_rate_limit_exception(error_text: str) -> bool:
    """Returns True when the captured cron-failure error_text looks like
    an upstream rate-limit response. Such failures should not trigger
    the default 3-attempt retry — the cron's natural schedule provides
    a better-shaped backoff than an immediate re-fire.
    """
    if not error_text:
        return False
    haystack = error_text.lower()
    return any(token in haystack for token in _RATE_LIMIT_BODY_TOKENS)


def _normalize_timeout_seconds(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return max(MIN_CRON_TIMEOUT_SECONDS, min(MAX_CRON_TIMEOUT_SECONDS, seconds))


# ── Hermes-F site-wiring timing instrumentation ─────────────────────────────
#
# The Hermes-F cron task-link helpers (``ensure_cron_task_link``,
# ``_task_hub_open_conn``, ``_close_run``, etc.) run *synchronous* SQLite
# work inside the cron service's *async* coroutines. Sync SQL inside async
# code blocks the entire event loop if the call hangs.
#
# After the 2026-05-12 gateway freeze (20.8h silence — see
# plans/2026-05-13_proactivity_gap_findings.md primary root cause), we
# need breadcrumbs in the journal so a future freeze can be diagnosed
# without operator intervention. The pattern is:
#
#     _t = _phase_f_start(job_id, "step_name")
#     <synchronous sql call>
#     _phase_f_done(job_id, "step_name", _t)
#
# ``_phase_f_start`` logs an entry marker at DEBUG (always present at that
# level, so a journal slice with DEBUG enabled shows every step).
# ``_phase_f_done`` logs the elapsed time:
#   - WARNING if > 5s — possible deadlock root cause
#   - INFO if > 500ms — slow, worth investigating
#   - DEBUG otherwise — normal, kept for full breadcrumb trail
#
# If a step's "entering" log appears with no corresponding "took N ms"
# log later in the journal, that step is the freeze site.

_PHASE_F_WARN_MS = 5000.0  # > 5s — flag as potential deadlock root cause
_PHASE_F_INFO_MS = 500.0   # > 500ms — flag as slow but not catastrophic


def _phase_f_start(job_id: str, step_name: str) -> float:
    """Log entry into a Hermes-F site-wiring step and return its start time.

    Always logs at DEBUG so the full breadcrumb trail is available when
    the journal is queried at that level. Returns ``time.perf_counter()``
    so ``_phase_f_done`` can compute the elapsed.
    """
    logger.debug("Phase F site-wiring [%s]: entering %s", job_id, step_name)
    return time.perf_counter()


def _phase_f_done(job_id: str, step_name: str, t0: float) -> None:
    """Log exit from a Hermes-F site-wiring step with elapsed time.

    Tiered logging so the journal isn't flooded:
      - WARNING if > 5s
      - INFO if > 500ms
      - DEBUG otherwise (matched pair with the entry log at DEBUG)
    """
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if elapsed_ms > _PHASE_F_WARN_MS:
        logger.warning(
            "Phase F site-wiring [%s]: %s took %.0fms "
            "(>5s — possible deadlock root cause)",
            job_id, step_name, elapsed_ms,
        )
    elif elapsed_ms > _PHASE_F_INFO_MS:
        logger.info(
            "Phase F site-wiring [%s]: %s took %.0fms",
            job_id, step_name, elapsed_ms,
        )
    else:
        logger.debug(
            "Phase F site-wiring [%s]: %s took %.1fms",
            job_id, step_name, elapsed_ms,
        )


def _resolve_run_at_timezone(timezone_name: str | None) -> Any:
    name = (timezone_name or "").strip() or "UTC"
    try:
        return pytz.timezone(name)
    except Exception:
        local_tz = datetime.now().astimezone().tzinfo
        return local_tz or pytz.UTC


def _parse_time_of_day(raw: str) -> tuple[int, int] | None:
    text = raw.strip().lower()
    if not text:
        return None
    if text.startswith("at "):
        text = text[3:].strip()
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = (match.group(3) or "").lower()
    if minute < 0 or minute > 59:
        return None
    if meridiem:
        if hour < 1 or hour > 12:
            return None
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    elif hour > 23:
        return None
    return hour, minute


def _parse_natural_run_at(value: str, now_dt: datetime) -> float | None:
    text = value.strip().lower()
    if not text:
        return None

    if text in {"now", "right now", "asap"}:
        return now_dt.timestamp()

    if text.startswith("in "):
        duration_raw = text[3:].strip()
        duration = _parse_duration_seconds(duration_raw, 0)
        if duration <= 0:
            word_match = re.match(
                r"^(\d+)\s*(minute|minutes|min|hour|hours|day|days)$",
                duration_raw,
            )
            if word_match:
                amount = int(word_match.group(1))
                unit = word_match.group(2)
                if unit.startswith(("minute", "min")):
                    duration = amount * 60
                elif unit.startswith("hour"):
                    duration = amount * 3600
                elif unit.startswith("day"):
                    duration = amount * 86400
        if duration > 0:
            return now_dt.timestamp() + duration

    day_offset = 0
    explicit_day = False
    remainder = text
    if remainder.startswith("tomorrow"):
        explicit_day = True
        day_offset = 1
        remainder = remainder[len("tomorrow"):].strip()
    elif remainder.startswith("today"):
        explicit_day = True
        day_offset = 0
        remainder = remainder[len("today"):].strip()
    elif remainder.startswith("tonight"):
        explicit_day = True
        day_offset = 0
        remainder = remainder[len("tonight"):].strip()
        if not remainder:
            remainder = "8:00pm"

    time_parts = _parse_time_of_day(remainder or text)
    if not time_parts:
        return None

    hour, minute = time_parts
    candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=day_offset)
    if candidate <= now_dt and not explicit_day:
        candidate += timedelta(days=1)
    elif candidate <= now_dt and explicit_day and day_offset == 0:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def _parse_run_at_duration_seconds(raw: str) -> int:
    text = str(raw or "").strip().lower()
    if not text:
        return 0
    if text.startswith("in "):
        text = text[3:].strip()

    compact = re.match(r"^(\d+)\s*([smhdw])$", text)
    if compact:
        amount = int(compact.group(1))
        unit = compact.group(2)
        multipliers = {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
            "w": 604800,
        }
        return amount * multipliers[unit]

    spaced = re.match(
        r"^(\d+)\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs|day|days|week|weeks)$",
        text,
    )
    if not spaced:
        return 0

    amount = int(spaced.group(1))
    unit = spaced.group(2)
    if unit.startswith(("second", "sec")):
        return amount
    if unit.startswith(("minute", "min")):
        return amount * 60
    if unit.startswith(("hour", "hr")):
        return amount * 3600
    if unit.startswith("day"):
        return amount * 86400
    if unit.startswith("week"):
        return amount * 604800
    return 0


def parse_run_at(
    value: str | float | None,
    now: float | None = None,
    timezone_name: str | None = None,
) -> float | None:
    """Parse run_at value to absolute timestamp.
    
    Accepts:
    - None: returns None
    - float: absolute timestamp (returned as-is)
    - str: relative duration like "20m", "2h", "1d"
    - str: absolute ISO timestamp (with or without timezone)
    - str: natural phrases like "1am", "tomorrow 9:15am", "in 90 minutes"
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    
    value = value.strip()
    if not value:
        return None
    
    tz = _resolve_run_at_timezone(timezone_name)
    now_ts = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now_ts, tz)

    natural_time_hint = (
        _parse_time_of_day(value) is not None
        or value.lower().startswith(("tomorrow", "today", "tonight", "at "))
    )

    # Try relative duration first (e.g., "20m", "2h")
    if not natural_time_hint:
        duration = _parse_run_at_duration_seconds(value)
        if duration > 0:
            return now_ts + duration

    # Unix timestamp as string
    if re.match(r"^\d{9,}(?:\.\d+)?$", value):
        try:
            return float(value)
        except Exception:
            pass
    
    # Try ISO timestamp
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = tz.localize(dt) if hasattr(tz, "localize") else dt.replace(tzinfo=tz)
        return dt.timestamp()
    except Exception:
        pass

    # Try natural language forms
    return _parse_natural_run_at(value, now_dt)

@dataclass
class CronJob:
    """Scheduled job definition supporting interval, cron-expression, and one-shot scheduling."""
    job_id: str
    user_id: str
    workspace_dir: str
    command: str
    description: Optional[str] = None
    every_seconds: int = 0  # Simple interval (mutually exclusive with cron_expr)
    cron_expr: Optional[str] = None  # 5-field cron expression (e.g., "0 7 * * 1")
    timezone: str = "UTC"  # Timezone for cron expression
    run_at: Optional[float] = None  # One-shot: absolute timestamp to run at
    delete_after_run: bool = False  # One-shot: delete job after successful run
    model: Optional[str] = None  # Model override for this job
    timeout_seconds: Optional[int] = None  # Per-job execution timeout
    enabled: bool = True
    catch_up_on_restart: bool = False  # If True, fire a backfill run for missed windows on service restart
    created_at: float = field(default_factory=lambda: time.time())
    last_run_at: Optional[float] = None
    next_run_at: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "workspace_dir": self.workspace_dir,
            "command": self.command,
            "description": self.description,
            "every_seconds": self.every_seconds,
            "cron_expr": self.cron_expr,
            "timezone": self.timezone,
            "run_at": self.run_at,
            "delete_after_run": self.delete_after_run,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "enabled": self.enabled,
            "catch_up_on_restart": self.catch_up_on_restart,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronJob":
        return cls(
            job_id=data["job_id"],
            user_id=data.get("user_id", f"cron:{data['job_id']}"),
            workspace_dir=data["workspace_dir"],
            command=data["command"],
            description=data.get("description"),
            every_seconds=int(data.get("every_seconds", 0)),
            cron_expr=data.get("cron_expr"),
            timezone=data.get("timezone", "UTC"),
            run_at=data.get("run_at"),
            delete_after_run=bool(data.get("delete_after_run", False)),
            model=data.get("model"),
            timeout_seconds=_normalize_timeout_seconds(
                data.get("timeout_seconds")
                if data.get("timeout_seconds") is not None
                else data.get("metadata", {}).get("timeout_seconds")
            ),
            enabled=bool(data.get("enabled", True)),
            catch_up_on_restart=bool(data.get("catch_up_on_restart", False)),
            created_at=float(data.get("created_at", time.time())),
            last_run_at=data.get("last_run_at"),
            next_run_at=data.get("next_run_at"),
            metadata=data.get("metadata", {}),
        )

    def schedule_next(self, now: float) -> None:
        """Calculate next run time based on scheduling type."""
        # One-shot: run_at is the only run time (no rescheduling)
        if self.run_at is not None:
            if self.last_run_at is None:
                self.next_run_at = self.run_at
            else:
                # Already ran, no next run
                self.next_run_at = None
            return

        # Cron expression takes precedence over interval
        if self.cron_expr:
            try:
                tz = pytz.timezone(self.timezone)
                base_dt = datetime.fromtimestamp(now, tz)
                cron = croniter(self.cron_expr, base_dt)
                next_dt = cron.get_next(datetime)
                self.next_run_at = next_dt.timestamp()
            except Exception as exc:
                logger.warning("Invalid chron expression '%s': %s", self.cron_expr, exc)
                # Fall back to interval if cron fails
                if self.every_seconds > 0:
                    self.next_run_at = now + self.every_seconds
                else:
                    self.next_run_at = None
            return

        # Simple interval scheduling
        if self.every_seconds <= 0:
            self.next_run_at = None
            return
        base = self.last_run_at or self.created_at
        candidate = base + self.every_seconds
        if candidate <= now:
            candidate = now + self.every_seconds
        self.next_run_at = candidate



@dataclass
class CronRunRecord:
    """Record of a single cron job execution attempt."""
    run_id: str
    job_id: str
    status: str
    scheduled_at: Optional[float]
    started_at: float
    workflow_run_id: Optional[str] = None
    workflow_attempt_id: Optional[str] = None
    workflow_attempt_number: Optional[int] = None
    dispatch_key: Optional[str] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    output_preview: Optional[str] = None
    session_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "status": self.status,
            "scheduled_at": self.scheduled_at,
            "started_at": self.started_at,
            "workflow_run_id": self.workflow_run_id,
            "workflow_attempt_id": self.workflow_attempt_id,
            "workflow_attempt_number": self.workflow_attempt_number,
            "dispatch_key": self.dispatch_key,
            "finished_at": self.finished_at,
            "error": self.error,
            "output_preview": self.output_preview,
            "session_id": self.session_id,
        }


class CronStore:
    """File-backed persistence for cron job definitions and run history."""
    def __init__(self, jobs_path: Path, runs_path: Path):
        self.jobs_path = jobs_path
        self.runs_path = runs_path
        # In-flight marker sidecar (next to cron_jobs.json). Persists
        # {job_id: {scheduled_at, marked_at}} for runs dispatched by the
        # scheduler so a deploy-restart that hard-kills the gateway leaves
        # durable evidence of the interrupted run. Without it, a killed
        # in-flight run writes no cron_runs.jsonl record and is invisible
        # to the startup backfill (which only inspects persisted
        # next_run_at). See the 2026-06-09/10 paper_to_podcast incident.
        self.inflight_path = jobs_path.with_name("cron_inflight.json")
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)

    def load_jobs(self) -> dict[str, CronJob]:
        """Load all persisted jobs from disk, skipping malformed entries."""
        if not self.jobs_path.exists():
            return {}
        try:
            payload = json.loads(self.jobs_path.read_text())
        except Exception as exc:
            logger.error("Failed to read cron jobs: %s", exc)
            return {}
        jobs = {}
        for item in payload.get("jobs", []):
            try:
                job = CronJob.from_dict(item)
                jobs[job.job_id] = job
            except Exception as exc:
                logger.warning("Skipping invalid cron job: %s", exc)
        return jobs

    def save_jobs(self, jobs: Iterable[CronJob]) -> None:
        """Persist the full job registry to disk."""
        data = {"jobs": [job.to_dict() for job in jobs]}
        self.jobs_path.write_text(json.dumps(data, indent=2))

    def load_inflight(self) -> dict[str, dict[str, Any]]:
        """Read the in-flight marker sidecar. Returns {} on any error."""
        if not self.inflight_path.exists():
            return {}
        try:
            payload = json.loads(self.inflight_path.read_text())
        except Exception as exc:
            logger.error("Failed to read cron in-flight markers: %s", exc)
            return {}
        markers = payload.get("inflight")
        return markers if isinstance(markers, dict) else {}

    def save_inflight(self, markers: dict[str, dict[str, Any]]) -> None:
        self.inflight_path.parent.mkdir(parents=True, exist_ok=True)
        self.inflight_path.write_text(json.dumps({"inflight": markers}, indent=2))

    def append_run(self, record: CronRunRecord) -> None:
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict()) + "\n")

    def read_runs(self, job_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        """Read run records, optionally filtered by job_id, newest last."""
        if not self.runs_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with self.runs_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        data = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    if job_id and data.get("job_id") != job_id:
                        continue
                    rows.append(data)
        except Exception as exc:
            logger.error("Failed reading cron runs: %s", exc)
        return rows[-limit:]


class CronService:
    """Manages scheduled cron job lifecycle: registration, dispatch, retry, and persistence."""

    @staticmethod
    def _workflow_admission_service() -> WorkflowAdmissionService:
        return WorkflowAdmissionService()

    @staticmethod
    def _runtime_db_connect():
        conn = connect_runtime_db(get_runtime_db_path())
        ensure_schema(conn)
        return conn

    def __init__(
        self,
        gateway: InProcessGateway,
        workspaces_dir: Path,
        event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        wake_callback: Optional[Callable[[str, str, str], None]] = None,
        system_event_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
        agent_event_sink: Optional[Callable[[str, Any], Any]] = None,
    ):
        self.gateway = gateway
        self.workspaces_dir = workspaces_dir
        self.running = False
        self.task: Optional[asyncio.Task] = None
        # The gateway's main event loop, captured in ``start()``. Used so
        # loop-affine scheduling (``_schedule_retry_run``) still works when
        # invoked from a worker thread — e.g. the lightweight ``!script``
        # finalize path runs inside ``asyncio.to_thread`` (see ``_run_job``).
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.jobs: Dict[str, CronJob] = {}
        self.running_jobs: set[str] = set()
        self.running_job_scheduled_at: dict[str, float] = {}
        self.max_concurrency = int(os.getenv("UA_CRON_MAX_CONCURRENCY", "2"))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self.event_sink = event_sink
        self.wake_callback = wake_callback
        self.system_event_callback = system_event_callback
        self.agent_event_sink = agent_event_sink
        self._backfill_queue: list[tuple[str, float]] = []  # (job_id, missed_at) pairs

        jobs_path = workspaces_dir / "cron_jobs.json"
        runs_path = workspaces_dir / "cron_runs.jsonl"
        self.store = CronStore(jobs_path, runs_path)
        # Defensive: skip loading persisted jobs in dev. CronService should not
        # have been constructed at all in dev (feature_flags.cron_enabled()
        # returns False), but if some startup path still instantiates it the
        # belt-and-suspenders guard here keeps the 53+ persisted prod cron jobs
        # from ticking on Kevin's desktop. Phase D 2026-05-11.
        from universal_agent.loop_control import (
            is_development_runtime,  # noqa: PLC0415 — lazy
        )
        if is_development_runtime():
            logger.info(
                "CronService started in dev mode — skipping persisted "
                "cron_jobs.json load (would have loaded %d job(s))",
                len(self.store.load_jobs()),
            )
            self.jobs = {}
        else:
            self.jobs = self.store.load_jobs()
        _now_ts = time.time()
        _needs_save = False
        _backfill_max_age = 86400  # Only backfill missed windows within the last 24 hours
        for job in self.jobs.values():
            if job.next_run_at is None or job.next_run_at < _now_ts:
                missed_at = job.next_run_at  # Preserve what was missed before rescheduling
                # Recalculate from now to prevent stale/wrong timestamps from
                # causing immediate catch-up fires on restart (fix #5: timezone double-fire).
                job.schedule_next(_now_ts)
                _needs_save = True
                # Queue backfill for catch-up-enabled jobs with a recent missed window
                if (
                    job.catch_up_on_restart
                    and job.enabled
                    and missed_at is not None
                    and (_now_ts - missed_at) < _backfill_max_age
                ):
                    self._backfill_queue.append((job.job_id, missed_at))
                    logger.info(
                        "🔄 Queued backfill for job %s (missed at %s, age %.0fs)",
                        job.job_id,
                        datetime.fromtimestamp(missed_at).isoformat(),
                        _now_ts - missed_at,
                    )
        if _needs_save:
            self.store.save_jobs(self.jobs.values())

        # ── Deploy-interrupted in-flight recovery ──────────────────────
        # The scheduler persists an in-flight marker at dispatch
        # (``_mark_inflight``) and clears it when the run finalizes
        # (except deploy-restart cancellations, which keep it on purpose).
        # Any marker still present at construction time is a run that was
        # killed mid-flight by the previous shutdown. For jobs that opted
        # into ``catch_up_on_restart`` we requeue the interrupted slot —
        # markers for other jobs (or stale/deleted ones) are dropped.
        self._inflight_requeue: list[tuple[str, float]] = []
        _leftover_inflight = self.store.load_inflight()
        if _leftover_inflight:
            for _marker_job_id, _marker in _leftover_inflight.items():
                _marker_job = self.jobs.get(_marker_job_id)
                try:
                    _interrupted_at = float((_marker or {}).get("scheduled_at"))
                except (TypeError, ValueError):
                    _interrupted_at = None
                if (
                    _marker_job is not None
                    and _marker_job.enabled
                    and _marker_job.catch_up_on_restart
                    and _interrupted_at is not None
                    and (_now_ts - _interrupted_at) < _backfill_max_age
                ):
                    self._inflight_requeue.append((_marker_job_id, _interrupted_at))
                    logger.info(
                        "🔄 Queued in-flight recovery for job %s "
                        "(interrupted run was scheduled %s)",
                        _marker_job_id,
                        datetime.fromtimestamp(_interrupted_at).isoformat(),
                    )
                else:
                    logger.info(
                        "Dropping stale/ineligible cron in-flight marker for job %s",
                        _marker_job_id,
                    )
            # Markers are consumed at startup; the requeue dispatch in
            # ``start()`` re-marks the jobs it actually fires.
            try:
                self.store.save_inflight({})
            except Exception as _inflight_exc:  # noqa: BLE001
                logger.warning(
                    "Failed to clear consumed cron in-flight markers: %s",
                    _inflight_exc,
                )

    async def start(self) -> None:
        """Start the scheduler loop and optionally fire queued backfill runs."""
        if self.running:
            return
        self.running = True
        # Capture the loop the scheduler runs on so off-loop callers (worker
        # threads spawned via asyncio.to_thread) can schedule coroutines back
        # onto it via run_coroutine_threadsafe.
        self._loop = asyncio.get_running_loop()
        self.task = asyncio.create_task(self._scheduler_loop())
        logger.info("⏱️ Chron service started (%d jobs)", len(self.jobs))
        # Requeue deploy-interrupted in-flight runs for catch_up_on_restart
        # jobs — ALWAYS, even when UA_CRON_BACKFILL_ON_RESTART=0. That
        # global default exists to prevent a startup stampede of EVERY
        # missed slot (see the 2026-05-16 incident below); interrupted
        # IN-FLIGHT runs of explicitly opted-in jobs are a bounded set
        # (at most UA_CRON_MAX_CONCURRENCY were in flight at the restart)
        # and must recover — see the 2026-06-09/10 paper_to_podcast
        # incident where two consecutive 9 PM runs were deploy-killed and
        # never re-ran. The dispatch key is deliberately NOT the original
        # ``scheduled:`` key: the interrupted attempt's workflow run may
        # still sit in status=running (the gateway died before finalize),
        # and re-admitting under the same dedup key would
        # attach_to_existing and silently skip the recovery run.
        for _ifq_job_id, _ifq_interrupted_at in self._inflight_requeue:
            _ifq_job = self.jobs.get(_ifq_job_id)
            if (
                _ifq_job
                and _ifq_job.enabled
                and _ifq_job.job_id not in self.running_jobs
            ):
                logger.info(
                    "🔄 Dispatching in-flight recovery run for job %s "
                    "(interrupted run was scheduled %s)",
                    _ifq_job_id,
                    datetime.fromtimestamp(_ifq_interrupted_at).isoformat(),
                )
                self.running_jobs.add(_ifq_job.job_id)
                self._mark_inflight(_ifq_job.job_id, _ifq_interrupted_at)
                _ifq_dispatch_key = (
                    f"inflight:{_ifq_job.job_id}:{int(_ifq_interrupted_at)}:"
                    f"{uuid.uuid4().hex[:8]}"
                )
                asyncio.create_task(
                    self._run_job(
                        _ifq_job,
                        scheduled_at=_ifq_interrupted_at,
                        reason="backfill",
                        dispatch_key=_ifq_dispatch_key,
                    )
                )
        self._inflight_requeue.clear()
        # Fire queued backfill runs for jobs that missed their window during
        # restart — but ONLY if explicitly enabled via env var. Default is
        # OFF because firing every missed heavy cron simultaneously at
        # gateway startup starves the asyncio event loop (atlas_direct_dispatch,
        # claude_code_intel_sync, etc. each do many HTTP calls + LLM work
        # in-process) and the gateway can't answer /api/v1/health, causing
        # deploy.yml health checks to time out at the 8-minute window.
        # Missed runs resume on the next normal cron tick — typically
        # within minutes for any frequent job. See 2026-05-16 incident.
        backfill_enabled = str(
            os.getenv("UA_CRON_BACKFILL_ON_RESTART") or "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if backfill_enabled:
            for job_id, missed_at in self._backfill_queue:
                job = self.jobs.get(job_id)
                if job and job.enabled and job.job_id not in self.running_jobs:
                    logger.info(
                        "🔄 Dispatching backfill run for job %s (originally scheduled %s)",
                        job_id,
                        datetime.fromtimestamp(missed_at).isoformat(),
                    )
                    self.running_jobs.add(job.job_id)
                    dispatch_key = self._dispatch_key_for_job(job, reason="backfill", scheduled_at=missed_at)
                    asyncio.create_task(
                        self._run_job(job, scheduled_at=missed_at, reason="backfill", dispatch_key=dispatch_key)
                    )
        elif self._backfill_queue:
            logger.info(
                "⏭️  Skipping %d queued backfill run(s) at startup "
                "(UA_CRON_BACKFILL_ON_RESTART=0 by default). Missed jobs "
                "will resume on next normal cron tick.",
                len(self._backfill_queue),
            )
        self._backfill_queue.clear()

    async def stop(self) -> None:
        """Cancel the scheduler loop and wait for it to finish."""
        if not self.running:
            return
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 Chron service stopped")

    def list_jobs(self) -> list[CronJob]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> Optional[CronJob]:
        return self.jobs.get(job_id)

    def add_job(
        self,
        user_id: str,
        workspace_dir: Optional[str],
        command: str,
        description: Optional[str] = None,
        every_raw: Optional[str] = None,
        cron_expr: Optional[str] = None,
        timezone: str = "UTC",
        run_at: Optional[float] = None,
        delete_after_run: bool = False,
        model: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        enabled: bool = True,
        catch_up_on_restart: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> CronJob:
        """Register a new cron job with validated scheduling parameters."""
        every_seconds = _parse_duration_seconds(every_raw, 0) if every_raw else 0

        # Validate scheduling - must have at least one method
        if every_seconds <= 0 and not cron_expr and run_at is None:
            raise ValueError("Must provide at least one of: every, cron_expr, or run_at")

        if 0 < every_seconds < MIN_CRON_INTERVAL_SECONDS:
            raise ValueError(
                f"every_seconds={every_seconds} is below the minimum interval "
                f"({MIN_CRON_INTERVAL_SECONDS}s). Use cron_expr for finer scheduling."
            )

        # Validate cron expression if provided
        if cron_expr:
            try:
                croniter(cron_expr)
            except Exception as exc:
                raise ValueError(f"Invalid chron expression '{cron_expr}': {exc}")
        
        # Validate timezone
        try:
            pytz.timezone(timezone)
        except Exception:
            raise ValueError(f"Invalid timezone '{timezone}'")
        
        job_id = uuid.uuid4().hex[:10]
        workspace = workspace_dir or str(self.workspaces_dir / f"cron_{job_id}")
        metadata = dict(metadata or {})
        if not metadata.get("session_id"):
            metadata["session_id"] = Path(workspace).name
        job = CronJob(
            job_id=job_id,
            user_id=user_id or f"cron:{job_id}",
            workspace_dir=workspace,
            command=command,
            description=description,
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            timezone=timezone,
            run_at=run_at,
            delete_after_run=delete_after_run,
            model=model,
            timeout_seconds=_normalize_timeout_seconds(timeout_seconds),
            enabled=enabled,
            catch_up_on_restart=catch_up_on_restart,
            metadata=metadata,
        )
        job.schedule_next(time.time())
        self.jobs[job_id] = job
        self.store.save_jobs(self.jobs.values())
        self._emit_event({"type": "cron_job_created", "job": job.to_dict()})
        return job

    def update_job(self, job_id: str, updates: dict[str, Any]) -> CronJob:
        """Apply partial updates to an existing job and recalculate its schedule."""
        job = self.jobs[job_id]
        if "command" in updates:
            job.command = updates["command"]
        if "description" in updates:
            job.description = updates["description"]
        if "enabled" in updates:
            job.enabled = bool(updates["enabled"])
        if "every" in updates or "every_seconds" in updates:
            raw = updates.get("every") or updates.get("every_seconds")
            new_every = _parse_duration_seconds(str(raw), job.every_seconds)
            if 0 < new_every < MIN_CRON_INTERVAL_SECONDS:
                raise ValueError(
                    f"every_seconds={new_every} is below the minimum interval "
                    f"({MIN_CRON_INTERVAL_SECONDS}s). Use cron_expr for finer scheduling."
                )
            job.every_seconds = new_every
        if "cron_expr" in updates:
            cron_expr = updates["cron_expr"]
            if cron_expr:
                try:
                    croniter(cron_expr)
                except Exception as exc:
                    raise ValueError(f"Invalid chron expression '{cron_expr}': {exc}")
            job.cron_expr = cron_expr
        if "timezone" in updates:
            tz = updates["timezone"]
            try:
                pytz.timezone(tz)
            except Exception:
                raise ValueError(f"Invalid timezone '{tz}'")
            job.timezone = tz
        if "run_at" in updates:
            job.run_at = updates["run_at"]
        if "delete_after_run" in updates:
            job.delete_after_run = bool(updates["delete_after_run"])
        if "model" in updates:
            job.model = updates["model"]
        if "timeout_seconds" in updates:
            job.timeout_seconds = _normalize_timeout_seconds(updates["timeout_seconds"])
        if "workspace_dir" in updates:
            job.workspace_dir = updates["workspace_dir"]
        if "user_id" in updates:
            job.user_id = updates["user_id"]
        if "catch_up_on_restart" in updates:
            job.catch_up_on_restart = bool(updates["catch_up_on_restart"])
        if "metadata" in updates and isinstance(updates["metadata"], dict):
            job.metadata.update(updates["metadata"])
        job.schedule_next(time.time())
        self.store.save_jobs(self.jobs.values())
        self._emit_event({"type": "cron_job_updated", "job": job.to_dict()})
        return job

    def delete_job(self, job_id: str) -> None:
        """Remove a job from the registry and cancel any in-flight retry tasks."""
        if job_id in self.jobs:
            self._emit_event({"type": "cron_job_deleted", "job_id": job_id})
            del self.jobs[job_id]
            self.store.save_jobs(self.jobs.values())
        # Cancel any in-flight asyncio retry tasks for this job. Without
        # this, a `_schedule_retry_run` chain started while the job was
        # alive will keep firing every retry tick, re-emitting
        # `cron_run_retry_queued` events forever — even though the job
        # is gone from `self.jobs` and `cron_jobs.json`. Observed live
        # on 2026-05-11 when test cron `2df80b6f95` was deleted via API
        # but kept generating retry-storm emails for 90+ minutes.
        # `_run_job` also defense-in-depths via a deleted-job guard at
        # its top so any retry tick that survives this cancel still
        # short-circuits cleanly.
        self.running_jobs.discard(job_id)
        self.running_job_scheduled_at.pop(job_id, None)

    def list_runs(self, job_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.read_runs(job_id=job_id, limit=limit)

    @staticmethod
    def _dispatch_key_for_job(job: CronJob, *, reason: str, scheduled_at: Optional[float]) -> str:
        if scheduled_at is not None:
            return f"scheduled:{job.job_id}:{int(float(scheduled_at))}"
        return f"manual:{job.job_id}:{uuid.uuid4().hex[:12]}"

    def _mark_inflight(self, job_id: str, scheduled_at: float) -> None:
        """Persist a durable in-flight marker for a dispatched run.

        Written BEFORE the ``_run_job`` task is created so a deploy
        restart that hard-kills the gateway mid-run leaves evidence the
        startup recovery pass (``__init__``) can requeue. Best-effort —
        marker failures must never block dispatch.
        """
        try:
            markers = self.store.load_inflight()
            markers[job_id] = {
                "scheduled_at": float(scheduled_at),
                "marked_at": time.time(),
            }
            self.store.save_inflight(markers)
        except Exception as exc:  # noqa: BLE001 — never block dispatch
            logger.warning(
                "Failed to persist cron in-flight marker for job %s: %s",
                job_id, exc,
            )

    def _clear_inflight(self, job_id: str) -> None:
        """Remove a job's in-flight marker after its run finalizes.

        Deliberately NOT called for deploy-restart cancellations — those
        keep their marker so the next gateway boot requeues the slot.
        """
        try:
            markers = self.store.load_inflight()
            if job_id in markers:
                del markers[job_id]
                self.store.save_inflight(markers)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug(
                "Failed to clear cron in-flight marker for job %s: %s",
                job_id, exc,
            )

    def _build_workflow_trigger(self, job: CronJob, *, dispatch_key: str) -> WorkflowTrigger:
        payload = {
            "job_id": job.job_id,
            "command": job.command,
            "workspace_dir": job.workspace_dir,
            "reason": dispatch_key.split(":", 1)[0],
            "metadata": job.metadata or {},
        }
        return WorkflowTrigger(
            run_kind="cron_job_dispatch",
            trigger_source="cron",
            dedup_key=dispatch_key,
            payload_json=json.dumps(payload, ensure_ascii=True, sort_keys=True),
            priority=100,
            run_policy="automation_ephemeral",
            interrupt_policy="attach_if_same_dedup_key",
            external_origin="cron",
            external_origin_id=job.job_id,
            external_correlation_id=dispatch_key,
        )

    def _attempt_number(self, attempt_id: Optional[str]) -> Optional[int]:
        if not attempt_id:
            return None
        conn = self._runtime_db_connect()
        try:
            row = get_run_attempt(conn, attempt_id)
            if row is None:
                return None
            return int(row["attempt_number"] or 0) or None
        finally:
            conn.close()

    # Per-cron model tier resolution. The LLM cron path (cron_service.py
    # ~line 1591) historically hard-coded ``force_complex=True``, which biases
    # the agent loop toward Opus-tier reasoning on ZAI (``glm-5.1``). For
    # low-complexity content crons (codie_cleanup, csi_demo_triage_rank,
    # proactive_artifact_digest, etc.) that's wasteful and exacerbates ZAI
    # Fair-Usage 429s. ``metadata.model_tier`` lets each cron declare its
    # required reasoning tier. Default remains ``"high"`` to preserve the
    # pre-change behavior for any cron that hasn't opted in.
    #
    # NOTE — Cody dispatch path is NOT affected by this helper. When a cron
    # enqueues a Cody mission via ``vp_dispatch_mission``, Cody's model
    # selection lives in ``vp/clients/claude_cli_client.py`` (her CLI env
    # build). The Cody-on-ZAI rule (Opus always, never downgrade) is
    # documented in ``memory/feedback_cody_on_zai_opus.md`` and enforced
    # there, not here.
    _MODEL_TIER_HIGH = ("high", "opus")
    _MODEL_TIER_LOW = ("low", "sonnet", "haiku")

    @classmethod
    def _force_complex_for_job(cls, job: CronJob) -> bool:
        """Resolve the ``force_complex`` flag from ``metadata.model_tier``.

        Returns True (Opus-tier reasoning) for unset or "high"/"opus",
        False (Sonnet-tier or lower) for "low"/"sonnet"/"haiku". Unknown
        values fall back to True so a typo never silently downgrades a
        critical cron.
        """
        metadata = getattr(job, "metadata", None) or {}
        tier = str(metadata.get("model_tier") or "").strip().lower()
        if not tier:
            return True  # preserves pre-2026-05-13 default
        if tier in cls._MODEL_TIER_LOW:
            return False
        if tier in cls._MODEL_TIER_HIGH:
            return True
        logger.warning(
            "Unknown model_tier=%r on cron job %s; falling back to force_complex=True",
            tier, job.job_id,
        )
        return True

    @staticmethod
    def _max_attempts_for_job(job: CronJob) -> int:
        """Resolve workflow ``max_attempts`` from ``metadata.max_attempts``.

        Default 3 preserves the pre-change behavior. Lower bound is 1
        (i.e., the first dispatch counts; no retries). Floats, bools,
        and other non-integer types fall back to the default rather
        than silently truncating (e.g., ``2.5`` should not become 2).
        """
        metadata = getattr(job, "metadata", None) or {}
        raw = metadata.get("max_attempts")
        # bool is a subclass of int in Python; reject it explicitly.
        # Only accept actual ints or integer-valued strings.
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            return 3
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 3
        return max(1, value)

    def _schedule_retry_run(
        self,
        *,
        job: CronJob,
        scheduled_at: Optional[float],
        reason: str,
        dispatch_key: str,
        workflow_run_id: str,
        workflow_attempt_id: str,
    ) -> None:
        self.running_jobs.add(job.job_id)

        def _build_retry_coro():
            return self._run_job(
                job,
                scheduled_at=scheduled_at,
                reason=reason,
                dispatch_key=dispatch_key,
                workflow_run_id=workflow_run_id,
                workflow_attempt_id=workflow_attempt_id,
                skip_workflow_admission=True,
            )

        # This helper is loop-affine but is reachable from two contexts:
        #   1. On the gateway event loop (most _finalize_workflow_attempt
        #      callers invoke it directly inside the async _run_job).
        #   2. From a worker thread — the lightweight `!script` finalize path
        #      runs `await asyncio.to_thread(self._finalize_workflow_attempt, …)`
        #      (the 2026-05-26 copytree hot-patch), and that thread has no
        #      running loop. A bare `asyncio.create_task` there raised
        #      `RuntimeError: no running event loop`, orphaning the _run_job
        #      coroutine (the 2026-05-28/29/30 "no running event loop" cron
        #      failures). Schedule onto the captured main loop instead.
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None:
            running_loop.create_task(_build_retry_coro())
            return

        main_loop = self._loop
        if main_loop is None or main_loop.is_closed():
            logger.error(
                "Cannot schedule retry for job %s: called off-loop and no "
                "captured main loop is available; retry dropped.",
                job.job_id,
            )
            self.running_jobs.discard(job.job_id)
            return
        asyncio.run_coroutine_threadsafe(_build_retry_coro(), main_loop)

    def _finalize_workflow_attempt(
        self,
        *,
        job: CronJob,
        record: CronRunRecord,
        scheduled_at: Optional[float],
        reason: str,
        dispatch_key: str,
        workflow_run_id: Optional[str],
        workflow_attempt_id: Optional[str],
        failure_reason: Optional[str] = None,
        failure_class: Optional[str] = None,
        retryable: bool = False,
    ) -> None:
        if not workflow_run_id or not workflow_attempt_id:
            return
        admission_service = self._workflow_admission_service()
        if record.status == "success":
            admission_service.mark_completed(
                workflow_run_id,
                attempt_id=workflow_attempt_id,
                summary={"job_id": job.job_id, "status": "success"},
            )
            # Surface the successful run as an intelligence-grade event
            # so Mission Control tier-1 can discover it as a card. The
            # operator wants to see "did we just run something useful?",
            # not just failures. Defensive: never let instrumentation
            # break the cron lifecycle.
            try:
                self._emit_cron_success_intelligence(job, record)
            except Exception as exc:
                logger.debug(
                    "cron success intelligence emit failed for %s: %s",
                    job.job_id, exc,
                )
            return
        if record.status == "auth_required":
            admission_service.mark_needs_review(
                workflow_run_id,
                attempt_id=workflow_attempt_id,
                reason=failure_reason or "auth_required",
                failure_class=failure_class or "auth_required",
                summary={"job_id": job.job_id, "status": "auth_required"},
            )
            return
        if retryable:
            retry_decision = admission_service.queue_retry(
                self._build_workflow_trigger(job, dispatch_key=dispatch_key),
                entrypoint="cron_service._run_job",
                run_id=workflow_run_id,
                attempt_id=workflow_attempt_id,
                workspace_dir=job.workspace_dir,
                failure_reason=failure_reason or "cron_dispatch_failed",
                failure_class=failure_class or "cron_dispatch_failed",
                max_attempts=self._max_attempts_for_job(job),
            )
            if retry_decision.action == "start_new_attempt" and retry_decision.attempt_id:
                next_attempt_number = self._attempt_number(retry_decision.attempt_id)
                record.status = "retry_queued"
                record.error = failure_reason or record.error
                self._emit_event(
                    {
                        "type": "cron_run_retry_queued",
                        "run": {
                            **record.to_dict(),
                            "next_attempt_id": retry_decision.attempt_id,
                            "next_attempt_number": next_attempt_number,
                        },
                        "reason": reason,
                    }
                )
                self._schedule_retry_run(
                    job=job,
                    scheduled_at=scheduled_at,
                    reason="retry",
                    dispatch_key=dispatch_key,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=retry_decision.attempt_id,
                )
                return
        admission_service.mark_needs_review(
            workflow_run_id,
            attempt_id=workflow_attempt_id,
            reason=failure_reason or record.error or "cron_dispatch_failed",
            failure_class=failure_class or "cron_dispatch_failed",
            summary={"job_id": job.job_id, "status": record.status},
        )

    async def run_job_now(
        self,
        job_id: str,
        reason: str = "manual",
        scheduled_at: Optional[float] = None,
        background: bool = False,
    ) -> CronRunRecord:
        """Immediately dispatch a job, either blocking or in the background."""
        job = self.jobs[job_id]
        if job.job_id in self.running_jobs:
            raise ValueError(f"Job {job.job_id} is already running")
        self.running_jobs.add(job.job_id)
        dispatch_key = self._dispatch_key_for_job(job, reason=reason, scheduled_at=scheduled_at)
        
        if background:
            asyncio.create_task(
                self._run_job(job, scheduled_at=scheduled_at, reason=reason, dispatch_key=dispatch_key)
            )
            return CronRunRecord(
                run_id=f"queued-{uuid.uuid4().hex[:8]}",
                job_id=job.job_id,
                status="queued",
                scheduled_at=scheduled_at,
                started_at=time.time(),
            )
        
        return await self._run_job(job, scheduled_at=scheduled_at, reason=reason, dispatch_key=dispatch_key)

    async def _scheduler_loop(self) -> None:
        while self.running:
            now = time.time()
            for job in list(self.jobs.values()):
                if not job.enabled:
                    continue
                if job.job_id in self.running_jobs:
                    continue
                if job.next_run_at is None:
                    job.schedule_next(now)
                if job.next_run_at and now >= job.next_run_at:
                    scheduled_at = float(job.next_run_at)
                    if job.run_at is not None:
                        job.next_run_at = now + 5.0
                    else:
                        job.last_run_at = now
                        job.schedule_next(now)
                    # Mark as running BEFORE dispatching to prevent race where
                    # the next scheduler tick fires a duplicate task before
                    # _run_job acquires the semaphore.
                    self.running_jobs.add(job.job_id)
                    self.store.save_jobs(self.jobs.values())
                    # Durable in-flight marker: save_jobs above already
                    # advanced last_run_at/next_run_at, so a restart-killed
                    # in-flight run would otherwise leave NO persisted
                    # evidence that this slot never actually completed.
                    self._mark_inflight(job.job_id, scheduled_at)
                    dispatch_key = self._dispatch_key_for_job(job, reason="schedule", scheduled_at=scheduled_at)
                    asyncio.create_task(
                        self._run_job(job, scheduled_at=scheduled_at, reason="schedule", dispatch_key=dispatch_key)
                    )
            await asyncio.sleep(1)

    @staticmethod
    def _find_missing_required_secrets(job: CronJob) -> list[str]:
        """Return the names of any env vars declared in
        `job.metadata["required_secrets"]` that resolve to an empty value.
        Jobs that don't declare required_secrets are skipped (return [])."""
        metadata = getattr(job, "metadata", None)
        if not isinstance(metadata, dict):
            return []
        required = metadata.get("required_secrets")
        if not isinstance(required, (list, tuple)):
            return []
        missing: list[str] = []
        for raw in required:
            name = str(raw or "").strip()
            if not name:
                continue
            value = os.getenv(name, "").strip()
            if not value:
                missing.append(name)
        return missing

    async def _run_job(
        self,
        job: CronJob,
        scheduled_at: Optional[float],
        reason: str,
        *,
        dispatch_key: Optional[str] = None,
        workflow_run_id: Optional[str] = None,
        workflow_attempt_id: Optional[str] = None,
        skip_workflow_admission: bool = False,
    ) -> CronRunRecord:
        # Deleted-job guard: short-circuit retries / scheduled runs for
        # jobs that have been removed from the registry since the task
        # was queued. Prevents the orphan-asyncio-task retry-storm
        # observed on 2026-05-11 (cron 2df80b6f95). The retry chain
        # `_finalize_workflow_attempt` → `_schedule_retry_run` →
        # `_run_job` would keep running off a stale `job` object reference
        # even after `delete_job` purged the registry, because the
        # asyncio task held the CronJob in closure. This guard breaks
        # the cycle.
        if job.job_id not in self.jobs:
            logger.info(
                "🛑 _run_job skipped — job %s was deleted from registry "
                "(orphan retry / scheduled tick discarded)",
                job.job_id,
            )
            self.running_jobs.discard(job.job_id)
            self.running_job_scheduled_at.pop(job.job_id, None)
            self._clear_inflight(job.job_id)
            return CronRunRecord(
                run_id=uuid.uuid4().hex[:12],
                job_id=job.job_id,
                status="skipped",
                scheduled_at=scheduled_at,
                started_at=time.time(),
                finished_at=time.time(),
                error="job_deleted_before_run",
                workflow_run_id=workflow_run_id,
                workflow_attempt_id=workflow_attempt_id,
                workflow_attempt_number=None,
                dispatch_key=dispatch_key,
            )
        # running_jobs is set by the caller (_scheduler_loop or run_job_now)
        # before dispatching, so no duplicate-guard needed here.
        async with self._semaphore:
            dispatch_key = dispatch_key or self._dispatch_key_for_job(job, reason=reason, scheduled_at=scheduled_at)
            admission_service = self._workflow_admission_service()
            trigger = self._build_workflow_trigger(job, dispatch_key=dispatch_key)
            if not skip_workflow_admission:
                decision = admission_service.admit(
                    trigger,
                    entrypoint="cron_service._run_job",
                    workspace_dir=job.workspace_dir,
                    max_attempts=self._max_attempts_for_job(job),
                )
                workflow_run_id = decision.run_id
                workflow_attempt_id = decision.attempt_id
                if decision.action in {"attach_to_existing_run", "defer", "skip_duplicate"}:
                    record = CronRunRecord(
                        run_id=uuid.uuid4().hex[:12],
                        job_id=job.job_id,
                        status="skipped",
                        scheduled_at=scheduled_at,
                        started_at=time.time(),
                        finished_at=time.time(),
                        error=decision.reason,
                        workflow_run_id=workflow_run_id,
                        workflow_attempt_id=workflow_attempt_id,
                        workflow_attempt_number=self._attempt_number(workflow_attempt_id),
                        dispatch_key=dispatch_key,
                    )
                    self.store.append_run(record)
                    self._emit_event({"type": "cron_run_completed", "run": record.to_dict(), "reason": reason})
                    self.running_jobs.discard(job.job_id)
                    self.running_job_scheduled_at.pop(job.job_id, None)
                    self._clear_inflight(job.job_id)
                    return record
                if decision.action == "escalate_review":
                    record = CronRunRecord(
                        run_id=uuid.uuid4().hex[:12],
                        job_id=job.job_id,
                        status="needs_review",
                        scheduled_at=scheduled_at,
                        started_at=time.time(),
                        finished_at=time.time(),
                        error=decision.reason,
                        workflow_run_id=workflow_run_id,
                        workflow_attempt_id=workflow_attempt_id,
                        workflow_attempt_number=self._attempt_number(workflow_attempt_id),
                        dispatch_key=dispatch_key,
                    )
                    self.store.append_run(record)
                    self._emit_event({"type": "cron_run_completed", "run": record.to_dict(), "reason": reason})
                    self.running_jobs.discard(job.job_id)
                    self.running_job_scheduled_at.pop(job.job_id, None)
                    self._clear_inflight(job.job_id)
                    return record
            record = CronRunRecord(
                run_id=uuid.uuid4().hex[:12],
                job_id=job.job_id,
                status="running",
                scheduled_at=scheduled_at,
                started_at=time.time(),
                workflow_run_id=workflow_run_id,
                workflow_attempt_id=workflow_attempt_id,
                workflow_attempt_number=self._attempt_number(workflow_attempt_id),
                dispatch_key=dispatch_key,
            )
            self._emit_event({"type": "cron_run_started", "run": record.to_dict(), "reason": reason})

            # ── Phase 5: pre-flight required-secrets check ─────────────
            # Each `_ensure_*_cron_job` may declare `metadata.required_secrets`
            # listing the env-var names the job needs to function (e.g. the
            # YouTube digest needs `<DAY>_YT_PLAYLIST`).  Verify them here
            # so a missing-key failure surfaces as a structured cron_run_failed
            # notification instead of the script firing-and-dying with no
            # operator-visible cause.
            missing_secrets = self._find_missing_required_secrets(job)
            if missing_secrets:
                record.status = "error"
                record.finished_at = time.time()
                record.error = (
                    f"Missing required secrets: {', '.join(missing_secrets)}. "
                    f"Configure these env vars (or Infisical entries) before "
                    f"the next scheduled run."
                )
                record.output_preview = record.error
                self.store.append_run(record)
                self._emit_event({
                    "type": "cron_run_completed",
                    "run": record.to_dict(),
                    "reason": reason,
                })
                self._finalize_workflow_attempt(
                    job=job,
                    record=record,
                    scheduled_at=scheduled_at,
                    reason=reason,
                    dispatch_key=dispatch_key,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                    failure_reason=record.error,
                    failure_class="missing_required_secrets",
                    retryable=False,
                )
                self.running_jobs.discard(job.job_id)
                self.running_job_scheduled_at.pop(job.job_id, None)
                self._clear_inflight(job.job_id)
                return record

            timeout_seconds = self._resolve_job_timeout_seconds(job)
            scheduled_marker = float(scheduled_at) if scheduled_at is not None else float(record.started_at)
            self.running_job_scheduled_at[job.job_id] = scheduled_marker
            try:
                if os.getenv("UA_CRON_MOCK_RESPONSE", "0").lower() in {"1", "true", "yes"}:
                    record.status = "success"
                    record.finished_at = time.time()
                    record.output_preview = "CRON_OK"
                    self._finalize_workflow_attempt(
                        job=job,
                        record=record,
                        scheduled_at=scheduled_at,
                        reason=reason,
                        dispatch_key=dispatch_key,
                        workflow_run_id=workflow_run_id,
                        workflow_attempt_id=workflow_attempt_id,
                    )
                elif (job.metadata or {}).get("lightweight"):
                    # Lightweight cron path. Pure-stdlib + sqlite3 housekeeping
                    # crons (e.g. simone_chat_auto_complete) bypass the
                    # heavyweight Claude-session bootstrap — Composio session
                    # creation, capability snapshot injection (~54 KB), SOUL
                    # load, dossier registration — that the standard cron path
                    # runs before every `!script` subprocess. That bootstrap
                    # synchronously stalls the gateway event loop for several
                    # seconds per cron tick, blowing past the dashboard's 4 s
                    # `/api/v1/version` client timeout and surfacing the red
                    # "Gateway unreachable" banner. See
                    # ``plans/fix-2-lightweight-cron-path.md``.
                    raw_command = job.command.strip()
                    if not raw_command.startswith("!script "):
                        raise RuntimeError(
                            "lightweight=True only supports `!script` commands, "
                            f"got: {raw_command!r}"
                        )
                    script_path = raw_command.replace("!script ", "", 1).strip()
                    script_argv = _parse_script_command_argv(raw_command)
                    logger.info(
                        "Chron job %s executing lightweight script: %s",
                        job.job_id, script_path,
                    )

                    import subprocess as _lw_subprocess
                    import sys as _lw_sys

                    _lw_env = os.environ.copy()
                    _lw_cwd = (
                        str(job.workspace_dir_resolved)
                        if hasattr(job, "workspace_dir_resolved")
                        else str(Path(__file__).resolve().parents[2])
                    )
                    _lw_project_src = str(Path(__file__).resolve().parents[1])
                    _lw_env["PYTHONPATH"] = (
                        f"{_lw_project_src}:{_lw_env.get('PYTHONPATH', '')}"
                    )

                    if workflow_run_id and workflow_attempt_id:
                        self._workflow_admission_service().mark_running(
                            workflow_run_id,
                            attempt_id=workflow_attempt_id,
                            provider_session_id=None,
                            summary={
                                "job_id": job.job_id,
                                "reason": reason,
                                "workspace_dir": job.workspace_dir,
                                "lightweight": True,
                            },
                        )

                    _lw_proc = await asyncio.create_subprocess_exec(
                        _lw_sys.executable,
                        "-m",
                        *script_argv,
                        stdout=_lw_subprocess.PIPE,
                        stderr=_lw_subprocess.PIPE,
                        cwd=_lw_cwd,
                        env=_lw_env,
                    )
                    _lw_was_timeout_killed = False
                    try:
                        _lw_stdout, _lw_stderr = await asyncio.wait_for(
                            _lw_proc.communicate(), timeout=timeout_seconds
                        )
                    except asyncio.TimeoutError:
                        _lw_was_timeout_killed = True
                        with contextlib.suppress(ProcessLookupError):
                            _lw_proc.kill()
                        try:
                            _lw_stdout, _lw_stderr = await asyncio.wait_for(
                                _lw_proc.communicate(), timeout=5
                            )
                        except Exception:
                            _lw_stdout, _lw_stderr = b"", b""
                        _lw_text = (
                            _lw_stdout.decode(errors="replace")
                            + "\n"
                            + _lw_stderr.decode(errors="replace")
                        )
                        _persist_cron_run_output(
                            job.workspace_dir,
                            job.job_id,
                            record.run_id,
                            _lw_proc.returncode,
                            _lw_text,
                        )
                        record.output_preview = _lw_text[:400]
                        raise

                    _lw_exit_code = _lw_proc.returncode
                    _lw_text = (
                        _lw_stdout.decode(errors="replace")
                        + "\n"
                        + _lw_stderr.decode(errors="replace")
                    )
                    _persist_cron_run_output(
                        job.workspace_dir,
                        job.job_id,
                        record.run_id,
                        _lw_exit_code,
                        _lw_text,
                    )

                    if _lw_exit_code == 0:
                        record.status = "success"
                        record.output_preview = _lw_text[:400]
                    elif (
                        _lw_exit_code is not None
                        and _lw_exit_code < 0
                        and _is_deploy_window_active()
                    ):
                        # Mirror the heavyweight `!script` deploy-window
                        # handling: signal-killed during a deploy restart is
                        # a benign cancellation, not a real failure.
                        _lw_signal = -_lw_exit_code
                        record.status = "cancelled"
                        record.error = (
                            f"subprocess killed by signal {_lw_signal} "
                            "during deploy restart (will re-fire on next gateway boot)"
                        )
                        record.output_preview = _lw_text[:400]
                        try:
                            job.next_run_at = (
                                time.time() + _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC
                            )
                            self.store.save_jobs(self.jobs.values())
                        except Exception as _lw_backfill_exc:  # noqa: BLE001
                            logger.warning(
                                "Lightweight cron %s: failed to advance next_run_at "
                                "after deploy-cancellation: %s",
                                job.job_id,
                                _lw_backfill_exc,
                            )
                    else:
                        record.status = "error"
                        record.error = f"Script exited with {_lw_exit_code}"
                        record.output_preview = _lw_text[:400]

                    record.finished_at = time.time()

                    class _LightweightResult:
                        def __init__(self, text: str) -> None:
                            self.response_text = text
                            self.session_id = ""

                    self._persist_run_output(
                        job, record, _LightweightResult(_lw_text)
                    )
                    _lw_failure_class = (
                        "cancelled"
                        if record.status == "cancelled"
                        else "script_exit_error"
                    )
                    # HOT-PATCH 2026-05-26: wrap in to_thread so the sync shutil.copytree
                    # inside mark_completed -> _sync_attempt_evidence (run_workspace.py:55)
                    # does not block the asyncio loop. Confirmed via py-spy stack dump.
                    await asyncio.to_thread(
                        self._finalize_workflow_attempt,
                        job=job,
                        record=record,
                        scheduled_at=scheduled_at,
                        reason=reason,
                        dispatch_key=dispatch_key,
                        workflow_run_id=workflow_run_id,
                        workflow_attempt_id=workflow_attempt_id,
                        failure_reason=record.error,
                        failure_class=_lw_failure_class,
                        retryable=(record.status == "error"),
                    )
                else:
                    configured_retries = 2
                    retries_raw = (os.getenv("UA_CRON_DB_LOCK_RETRIES") or "").strip()
                    if retries_raw:
                        try:
                            configured_retries = int(retries_raw)
                        except ValueError:
                            configured_retries = 2
                    max_db_lock_retries = max(0, min(_CRON_DB_LOCK_RETRY_MAX, configured_retries))

                    attempt = 0
                    while True:
                        try:
                            session = await self.gateway.create_session(
                                user_id=job.user_id,
                                workspace_dir=job.workspace_dir,
                            )
                            record.session_id = str(getattr(session, "session_id", "") or "")
                            if workflow_run_id and workflow_attempt_id:
                                admission_service.mark_running(
                                    workflow_run_id,
                                    attempt_id=workflow_attempt_id,
                                    provider_session_id=record.session_id or None,
                                    summary={
                                        "job_id": job.job_id,
                                        "reason": reason,
                                        "workspace_dir": job.workspace_dir,
                                    },
                                )
                            # Tag session so the reaper correctly classifies this as
                            # an admin (short-lived) session with cron TTL.
                            session_metadata = getattr(session, "metadata", None)
                            if session_metadata is None:
                                try:
                                    session_metadata = {}
                                    setattr(session, "metadata", session_metadata)
                                except Exception:
                                    session_metadata = None
                            if isinstance(session_metadata, dict):
                                session_metadata.setdefault("source", "cron")
                                session_metadata.setdefault("job_id", job.job_id)
                                session_metadata.setdefault("session_role", "cron")
                                session_metadata.setdefault("run_kind", "cron")
                                session_metadata.setdefault("skip_heartbeat", True)
                            # Build request metadata with optional model override
                            request_metadata: dict[str, Any] = {
                                "source": "cron",
                                "job_id": job.job_id,
                                "reason": reason,
                            }
                            if job.model:
                                request_metadata["model"] = job.model

                            # Plumb per-job wall-clock budget down to the
                            # execution engine so the in-process LLM turn
                            # respects the cron's configured timeout instead
                            # of the tier default. Without this, a cron with
                            # ``timeout_seconds=3600`` still gets killed at
                            # the opus tier default because the outer
                            # ``asyncio.wait_for`` and the engine's deadline
                            # are independent. See ProcessTurnAdapter's
                            # ``turn_timeout_seconds`` lookup in
                            # ``execution_engine.py``.
                            _job_timeout = self._resolve_job_timeout_seconds(job)
                            if _job_timeout is not None and _job_timeout > 0:
                                request_metadata["turn_timeout_seconds"] = int(_job_timeout)

                            # Plumb a per-job agentic-loop turn cap
                            # (max_turns) down to the execution engine.
                            # Set on a cron job's metadata -- e.g.
                            # paper_to_podcast_daily resolves
                            # UA_PAPER_TO_PODCAST_MAX_TURNS into this key --
                            # so jobs that need more than the engine default
                            # (20) turns can survive a long final phase
                            # (audio poll + download + email) instead of
                            # ending mid-run. Honored by
                            # gateway._resolve_max_turns_override. See
                            # RCA_paper_to_podcast.md (turn-budget
                            # exhaustion, 2026-06-16).
                            _job_max_turns = (job.metadata or {}).get("max_turns")
                            if _job_max_turns is not None:
                                request_metadata["max_turns"] = _job_max_turns

                            raw_command = job.command.strip()
                            if raw_command.startswith("!script "):
                                script_path = raw_command.replace("!script ", "", 1).strip()
                                script_argv = _parse_script_command_argv(raw_command)
                                logger.info(f"Chron job {job.job_id} executing native script: {script_path}")

                                # Use asyncio.create_subprocess_exec
                                import subprocess
                                import sys

                                env = os.environ.copy()
                                cwd_str = str(job.workspace_dir_resolved) if hasattr(job, "workspace_dir_resolved") else str(Path(__file__).resolve().parents[2])
                                project_src = str(Path(__file__).resolve().parents[1])
                                env["PYTHONPATH"] = f"{project_src}:{env.get('PYTHONPATH', '')}"

                                # Hermes Phase F site-wiring (cron `!script`).
                                # Resolve / auto-ensure the linked Task Hub
                                # task + assignment BEFORE spawning so we can
                                # stamp the subprocess PID onto the assignment
                                # row right after spawn.
                                #
                                # Post-PR #238 (Hermes-F cron task-link
                                # backfill, 2026-05-11): every cron `!script`
                                # job gets a stable `cron:<system_job>` task
                                # row auto-ensured by
                                # ``ensure_cron_task_link`` unless its
                                # metadata carries ``skip_task_hub_link =
                                # True`` (housekeeping crons opt out).  The
                                # email scheduler path (which already
                                # populates ``metadata.task_id``) is honored
                                # verbatim — the helper returns the explicit
                                # task_id and lets the existing
                                # ``find_active_assignment_for_task`` lookup
                                # run below to discover its assignment.
                                _f_job_metadata = job.metadata or {}
                                _f_task_id = ""
                                _f_assignment_id: Optional[str] = None
                                _f_auto_linked = False
                                _f_was_timeout_killed = False

                                try:
                                    from universal_agent.gateway_server import (
                                        _task_hub_open_conn as _f_link_open_conn,
                                    )
                                    from universal_agent.services.cron_task_hub_link import (
                                        ensure_cron_task_link as _f_ensure_link,
                                    )
                                    _t_open = _phase_f_start(job.job_id, "script.open_conn")
                                    _f_link_conn = _f_link_open_conn()
                                    _phase_f_done(job.job_id, "script.open_conn", _t_open)
                                    try:
                                        _t_link = _phase_f_start(job.job_id, "script.ensure_link")
                                        _f_linkage = _f_ensure_link(
                                            _f_link_conn,
                                            job_id=job.job_id,
                                            job_metadata=_f_job_metadata,
                                            description=(job.description or job.command)[:500],
                                        )
                                        _phase_f_done(job.job_id, "script.ensure_link", _t_link)
                                    finally:
                                        _f_link_conn.close()
                                    if _f_linkage:
                                        _f_task_id = str(_f_linkage.get("task_id") or "").strip()
                                        _f_assignment_id = str(_f_linkage.get("assignment_id") or "").strip() or None
                                        # `_f_auto_linked` distinguishes the
                                        # auto-ensured stable cron task from
                                        # an externally-supplied task_id
                                        # (email scheduler).  Only auto-linked
                                        # tasks get their status flipped back
                                        # to `open` after a clean run; the
                                        # email path manages its own
                                        # lifecycle.
                                        _f_auto_linked = not str(
                                            _f_job_metadata.get("task_id") or ""
                                        ).strip()
                                except Exception as _f_link_exc:
                                    logger.debug(
                                        "Phase F cron task-link skipped for job %s: %s",
                                        job.job_id, _f_link_exc,
                                    )

                                proc = await asyncio.create_subprocess_exec(
                                    sys.executable, "-m", *script_argv,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    cwd=cwd_str,
                                    env=env,
                                )
                                # Phase F.1 — record worker_pid on the linked
                                # assignment.  When auto-linked, the helper
                                # already created the assignment row and
                                # populated `_f_assignment_id`; for the
                                # email-scheduler path we still need to look
                                # it up via the classifier helper.  Best-
                                # effort throughout; never blocks the happy
                                # path.
                                if _f_task_id:
                                    try:
                                        from universal_agent import (
                                            task_hub as _f_th,
                                        )
                                        from universal_agent.gateway_server import (
                                            _task_hub_open_conn as _f_open_conn,
                                        )
                                        from universal_agent.services.worker_exit_classifier import (
                                            find_active_assignment_for_task as _f_find_aid,
                                        )
                                        _t_pid_open = _phase_f_start(job.job_id, "script.pid_stamp_open_conn")
                                        _f_conn = _f_open_conn()
                                        _phase_f_done(job.job_id, "script.pid_stamp_open_conn", _t_pid_open)
                                        try:
                                            if not _f_assignment_id:
                                                _t_find = _phase_f_start(job.job_id, "script.find_assignment")
                                                _f_assignment_id = _f_find_aid(
                                                    _f_conn, task_id=_f_task_id,
                                                )
                                                _phase_f_done(job.job_id, "script.find_assignment", _t_find)
                                            if _f_assignment_id and proc.pid:
                                                _t_pid = _phase_f_start(job.job_id, "script.record_worker_pid")
                                                _f_th.record_worker_pid(
                                                    _f_conn,
                                                    assignment_id=_f_assignment_id,
                                                    worker_pid=int(proc.pid),
                                                )
                                                _f_conn.commit()
                                                _phase_f_done(job.job_id, "script.record_worker_pid", _t_pid)
                                        finally:
                                            _f_conn.close()
                                    except Exception as _f_pid_exc:
                                        logger.debug(
                                            "Phase F.1 record_worker_pid skipped for cron job %s: %s",
                                            job.job_id, _f_pid_exc,
                                        )

                                try:
                                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
                                except asyncio.TimeoutError:
                                    _f_was_timeout_killed = True
                                    with contextlib.suppress(ProcessLookupError):
                                        proc.kill()
                                    try:
                                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
                                    except Exception:
                                        stdout, stderr = b"", b""
                                    output_text = stdout.decode(errors="replace") + "\n" + stderr.decode(errors="replace")
                                    _persist_cron_run_output(
                                        job.workspace_dir,
                                        job.job_id,
                                        record.run_id,
                                        proc.returncode,
                                        output_text,
                                    )
                                    record.output_preview = output_text[:400]
                                    raise

                                exit_code = proc.returncode
                                output_text = stdout.decode(errors="replace") + "\n" + stderr.decode(errors="replace")
                                _persist_cron_run_output(
                                    job.workspace_dir,
                                    job.job_id,
                                    record.run_id,
                                    exit_code,
                                    output_text,
                                )

                                if exit_code == 0:
                                    record.status = "success"
                                    record.output_preview = output_text[:400]
                                elif exit_code is not None and exit_code != 0 and _is_deploy_window_active():
                                    # Subprocess died while a deploy was in
                                    # flight. Two flavours, both deploy-restart
                                    # collateral rather than a real failure:
                                    #
                                    #   * NEGATIVE rc (e.g. -15): the kernel
                                    #     SIGTERM'd the subprocess as the gateway
                                    #     was torn down.
                                    #   * POSITIVE rc (e.g. 1): the subprocess
                                    #     ran to completion but failed because the
                                    #     platform was restarting under it — the
                                    #     classic case is the 2026-05-29
                                    #     `evening_briefing` incident, where the
                                    #     briefings_agent script exited rc=1 after
                                    #     `connect ECONNREFUSED ::1:8002` because
                                    #     the gateway it calls was mid-restart.
                                    #
                                    # In BOTH cases the cron itself is fine; the
                                    # platform yanked the rug. Mirror the
                                    # asyncio.CancelledError handling below: mark
                                    # cancelled, advance next_run_at so the next
                                    # scheduler tick re-fires this job, and skip
                                    # the retry chain entirely. The notifier's
                                    # existing `cancelled` branch already routes
                                    # this to a benign [INFO] alert instead of
                                    # the scary [ERROR] + [WARNING] pair.
                                    #
                                    # GUARDRAIL: the deploy-window predicate is
                                    # the ONLY thing that downgrades a nonzero
                                    # exit here. Outside a deploy window the
                                    # `else` branch still marks the run `error`
                                    # and the [ERROR] email fires exactly as
                                    # before — real failures are never suppressed
                                    # on the basis of rc alone.
                                    if exit_code < 0:
                                        signal_num = -exit_code
                                        _cancel_detail = (
                                            f"subprocess killed by signal {signal_num} "
                                            "during deploy restart"
                                        )
                                        _log_detail = f"killed by signal {signal_num}"
                                    else:
                                        _cancel_detail = (
                                            f"subprocess exited rc={exit_code} "
                                            "during deploy restart (platform unreachable mid-restart)"
                                        )
                                        _log_detail = f"exited rc={exit_code}"
                                    record.status = "cancelled"
                                    record.error = (
                                        f"{_cancel_detail} "
                                        "(will re-fire on next gateway boot)"
                                    )
                                    record.output_preview = output_text[:400]
                                    # Reschedule to fire shortly after gateway
                                    # boot. The startup pass at
                                    # cron_service.py:604-624 will see
                                    # next_run_at < now, recalculate to a fresh
                                    # schedule, and (because catch_up_on_restart
                                    # is set for the relevant LLM/intel crons)
                                    # queue a backfill run for the missed
                                    # window. For jobs without catch_up_on_restart,
                                    # the next regular scheduled fire picks it
                                    # up — no work is lost on idempotent crons.
                                    try:
                                        job.next_run_at = time.time() + _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC
                                        self.store.save_jobs(self.jobs.values())
                                        logger.info(
                                            "Chron job %s: subprocess %s "
                                            "during deploy window — marked cancelled, "
                                            "next_run_at advanced to +%ds for backfill on next boot",
                                            job.job_id,
                                            _log_detail,
                                            _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC,
                                        )
                                    except Exception as backfill_exc:  # noqa: BLE001
                                        logger.warning(
                                            "Chron job %s: failed to advance next_run_at "
                                            "after deploy-cancellation: %s",
                                            job.job_id,
                                            backfill_exc,
                                        )
                                else:
                                    record.status = "error"
                                    record.error = f"Script exited with {exit_code}"
                                    record.output_preview = output_text[:400]

                                # Phase F.1 / F.3 — classify exit and, if a
                                # protocol violation (rc=0 but linked task is
                                # still in_progress), route the task into
                                # needs_review for Phase B.1 unstick verbs to
                                # act on.
                                #
                                # Auto-linked cron tasks: the cron script
                                # itself doesn't know about the Task Hub row,
                                # so a clean rc=0 exit is the normal happy
                                # path — not a protocol violation.  Before
                                # the classifier runs we flip the auto-
                                # linked task to ``completed`` so
                                # ``task_was_closed_normally`` returns True
                                # and the classifier picks
                                # ``clean_exit_zero``.  After the close-run
                                # records the assignment outcome,
                                # ``close_cron_task_link`` flips the
                                # perpetual task back to ``open`` so the
                                # next cron tick can reuse it.  Email-
                                # scheduler crons (``_f_auto_linked = False``)
                                # keep the pre-existing semantics.
                                if _f_task_id:
                                    try:
                                        from universal_agent import (
                                            task_hub as _f_th2,
                                        )
                                        from universal_agent.gateway_server import (
                                            _task_hub_open_conn as _f_open_conn2,
                                        )
                                        from universal_agent.services.cron_task_hub_link import (
                                            close_cron_task_link as _f_close_link,
                                        )
                                        from universal_agent.services.worker_exit_classifier import (
                                            classify_worker_exit as _f_classify,
                                            park_task_for_protocol_violation as _f_park,
                                            task_was_closed_normally as _f_closed,
                                        )
                                        _f_conn2 = _f_open_conn2()
                                        try:
                                            # Pre-close the auto-linked task
                                            # on a clean exit so F.3 doesn't
                                            # treat the normal cron lifecycle
                                            # as a protocol violation.  We
                                            # flip the status directly via
                                            # SQL rather than calling
                                            # ``perform_task_action`` —
                                            # auto-linked cron tasks are
                                            # never email-bound, so the
                                            # verified-delivery gate that
                                            # ``perform_task_action`` runs
                                            # would just add noise.
                                            if (
                                                _f_auto_linked
                                                and exit_code == 0
                                                and not _f_was_timeout_killed
                                            ):
                                                try:
                                                    _f_conn2.execute(
                                                        "UPDATE task_hub_items "
                                                        "SET status = ?, seizure_state = ?, updated_at = ? "
                                                        "WHERE task_id = ?",
                                                        (
                                                            _f_th2.TASK_STATUS_COMPLETED,
                                                            "unseized",
                                                            _f_th2._now_iso(),
                                                            _f_task_id,
                                                        ),
                                                    )
                                                    _f_conn2.commit()
                                                except Exception as _f_complete_exc:
                                                    logger.debug(
                                                        "Phase F auto-link pre-close skipped for cron job %s: %s",
                                                        job.job_id, _f_complete_exc,
                                                    )

                                            _f_was_signaled = bool(
                                                exit_code is not None
                                                and exit_code < 0
                                                and not _f_was_timeout_killed
                                            )
                                            _f_closed_normally = _f_closed(
                                                _f_conn2, task_id=_f_task_id,
                                            )
                                            _f_classification = _f_classify(
                                                return_code=exit_code,
                                                was_signaled=_f_was_signaled,
                                                was_timeout_killed=_f_was_timeout_killed,
                                                task_closed_normally=_f_closed_normally,
                                            )
                                            logger.info(
                                                "Phase F.1 cron job %s exit classified as %s "
                                                "(task=%s, assignment=%s, rc=%s, auto_linked=%s)",
                                                job.job_id, _f_classification.outcome,
                                                _f_task_id, _f_assignment_id or "<none>",
                                                exit_code, _f_auto_linked,
                                            )
                                            if _f_assignment_id:
                                                try:
                                                    _f_th2._close_run(
                                                        _f_conn2,
                                                        assignment_id=_f_assignment_id,
                                                        outcome=(
                                                            "completed"
                                                            if exit_code == 0
                                                            else "failed"
                                                        ),
                                                        summary=f"cron !script {script_path}",
                                                        error=(record.error or "")[:500],
                                                        metadata={
                                                            "worker_exit": _f_classification.to_dict(),
                                                            "site": "cron",
                                                            "auto_linked": _f_auto_linked,
                                                        },
                                                    )
                                                    # Also mark the
                                                    # assignment itself as
                                                    # ended so subsequent
                                                    # ``find_active_assignment_for_task``
                                                    # lookups don't keep
                                                    # returning this row.
                                                    _f_conn2.execute(
                                                        "UPDATE task_hub_assignments "
                                                        "SET state = ?, ended_at = ? "
                                                        "WHERE assignment_id = ? AND ended_at IS NULL",
                                                        (
                                                            "completed" if exit_code == 0 else "failed",
                                                            _f_th2._now_iso(),
                                                            _f_assignment_id,
                                                        ),
                                                    )
                                                    _f_conn2.commit()
                                                except Exception as _f_close_exc:
                                                    logger.debug(
                                                        "Phase F.1 _close_run skipped for cron job %s: %s",
                                                        job.job_id, _f_close_exc,
                                                    )
                                            if _f_classification.is_protocol_violation:
                                                _f_park(
                                                    _f_conn2,
                                                    task_id=_f_task_id,
                                                    site="cron",
                                                    summary=f"cron !script {script_path} job={job.job_id}",
                                                    agent_id="cron_scheduler",
                                                )

                                            # Auto-linked tasks: flip
                                            # perpetual task back to
                                            # ``open`` so the next cron
                                            # tick can re-claim it.
                                            # ``close_cron_task_link``
                                            # checks status to avoid
                                            # stomping needs_review left
                                            # by F.3.
                                            if _f_auto_linked:
                                                _f_close_link(
                                                    _f_conn2,
                                                    task_id=_f_task_id,
                                                    success=(exit_code == 0),
                                                )
                                        finally:
                                            _f_conn2.close()
                                    except Exception as _f_exc:
                                        logger.debug(
                                            "Phase F.1/F.3 wiring skipped for cron job %s: %s",
                                            job.job_id, _f_exc,
                                        )

                                # Manually construct a result object to satisfy _persist_run_output
                                class _MockResult:
                                    def __init__(self, text):
                                        self.response_text = text
                                        self.session_id = str(getattr(session, "session_id", "") or "")
                                result = _MockResult(output_text)
                                record.finished_at = time.time()
                                self._persist_run_output(job, record, result)
                                # Pick failure_class to match the run status. The
                                # `cancelled` branch (added 2026-05-14, deploy-
                                # restart detection) needs the same label as the
                                # asyncio.CancelledError handler downstream so
                                # the admission service doesn't queue a retry.
                                if record.status == "cancelled":
                                    _final_failure_class = "cancelled"
                                else:
                                    _final_failure_class = "script_exit_error"
                                self._finalize_workflow_attempt(
                                    job=job,
                                    record=record,
                                    scheduled_at=scheduled_at,
                                    reason=reason,
                                    dispatch_key=dispatch_key,
                                    workflow_run_id=workflow_run_id,
                                    workflow_attempt_id=workflow_attempt_id,
                                    failure_reason=record.error,
                                    failure_class=_final_failure_class,
                                    retryable=record.status == "error",
                                )
                                break
                            else:
                                # Standard LLM cron execution
                                try:
                                    from universal_agent.artifacts import (
                                        resolve_artifacts_dir,
                                    )
                                    _artifacts_dir = str(resolve_artifacts_dir())
                                except Exception:
                                    _artifacts_dir = os.getenv("UA_ARTIFACTS_DIR", "").strip()
                                if _artifacts_dir:
                                    resolved_command = (
                                        f"[SYSTEM CONTEXT: UA_ARTIFACTS_DIR={_artifacts_dir}]\n\n"
                                        + job.command
                                    )
                                else:
                                    resolved_command = job.command

                                # Hermes Phase F site-wiring (cron LLM path).
                                # Mirrors the `!script` branch above: ensure a
                                # stable ``cron:<system_job>`` task + open an
                                # assignment row before invoking the LLM so we
                                # can record an outcome on the way out.
                                # LLM crons are in-process, so ``worker_pid``
                                # stays NULL. The try/finally below ensures
                                # the close-out fires even on timeout/error.
                                # All F wiring is best-effort — never breaks
                                # LLM execution.
                                #
                                # See docs/03_Operations/108_Task_Hub_Observability_Protocol.md.
                                _f_job_metadata = job.metadata or {}
                                _f_skip_link = bool(
                                    _f_job_metadata.get("skip_task_hub_link")
                                )
                                _f_task_id = ""
                                _f_assignment_id: Optional[str] = None
                                _f_auto_linked = False
                                _f_was_timeout_killed = False
                                _f_was_exception = False
                                # Tracks ``asyncio.CancelledError`` separately
                                # from timeouts and exceptions. The gateway's
                                # session reaper invokes ``task.cancel()`` on
                                # the in-process LLM coroutine when the
                                # 600s TTL elapses. ``CancelledError`` inherits
                                # from ``BaseException`` so it bypasses the
                                # generic ``except Exception`` block below —
                                # without explicit detection, ``_f_rc_equiv_llm``
                                # falls through to 0 and the F.1 classifier
                                # mis-paints the reap as ``clean_exit_zero``.
                                # See plans/2026-05-13_proactivity_gap_findings.md
                                # Contributing Factor #3.
                                _f_was_cancelled = False
                                # Tracks the deploy-kill signature: the SDK's
                                # claude CLI subprocess was SIGTERM'd by a
                                # deploy restart (exit 143), the SDK swallowed
                                # the message-reader fatal, and run_query
                                # returned an EMPTY result without raising.
                                # Without this flag, ``_f_rc_equiv_llm`` falls
                                # through to 0 and the F.1 classifier paints
                                # the kill as ``clean_exit_zero`` — the task is
                                # marked completed and the artifact notifier
                                # fires off stale artifacts. Mirrors the
                                # `!script` branch's deploy-window handling
                                # (and PR #563's deploy-window suppression
                                # precedent).
                                _f_was_deploy_killed = False
                                _f_run_error_text = ""
                                if not _f_skip_link:
                                    try:
                                        from universal_agent.gateway_server import (
                                            _task_hub_open_conn as _f_link_open_conn_llm,
                                        )
                                        from universal_agent.services.cron_task_hub_link import (
                                            ensure_cron_task_link as _f_ensure_link_llm,
                                        )
                                        _t_llm_open = _phase_f_start(job.job_id, "llm.open_conn")
                                        _f_link_conn_llm = _f_link_open_conn_llm()
                                        _phase_f_done(job.job_id, "llm.open_conn", _t_llm_open)
                                        try:
                                            _t_llm_link = _phase_f_start(job.job_id, "llm.ensure_link")
                                            _f_linkage = _f_ensure_link_llm(
                                                _f_link_conn_llm,
                                                job_id=job.job_id,
                                                job_metadata=_f_job_metadata,
                                                description=(job.description or job.command)[:500],
                                            )
                                            _phase_f_done(job.job_id, "llm.ensure_link", _t_llm_link)
                                        finally:
                                            _f_link_conn_llm.close()
                                        if _f_linkage:
                                            _f_task_id = str(_f_linkage.get("task_id") or "").strip()
                                            _f_assignment_id = str(_f_linkage.get("assignment_id") or "").strip() or None
                                            _f_auto_linked = not str(
                                                _f_job_metadata.get("task_id") or ""
                                            ).strip()
                                    except Exception as _f_link_exc:
                                        logger.debug(
                                            "Phase F LLM cron task-link skipped for job %s: %s",
                                            job.job_id, _f_link_exc,
                                        )

                                request = GatewayRequest(
                                    user_input=resolved_command,
                                    force_complex=self._force_complex_for_job(job),
                                    metadata=request_metadata,
                                )

                                async def _fire_event(evt: Any) -> None:
                                    if self.agent_event_sink:
                                        try:
                                            # Some event_sinks might expect awaitable
                                            res = self.agent_event_sink(session.session_id, evt)
                                            if hasattr(res, "__await__"):
                                                await res
                                        except Exception as e:
                                            logger.warning("Error dispatching chron agent event to UI sink: %s", e)

                                run_coro = self.gateway.run_query(session, request, event_callback=_fire_event)
                                try:
                                    try:
                                        if timeout_seconds is not None:
                                            result = await asyncio.wait_for(run_coro, timeout=timeout_seconds)
                                        else:
                                            result = await run_coro
                                    except asyncio.TimeoutError:
                                        _f_was_timeout_killed = True
                                        _f_run_error_text = (
                                            f"LLM cron timed out after {timeout_seconds}s"
                                        )
                                        raise
                                    except asyncio.CancelledError:
                                        # Session reaper or operator cancellation.
                                        # MUST be re-raised so the cancellation
                                        # propagates correctly to the asyncio
                                        # runtime; swallowing it would resume the
                                        # coroutine and break asyncio's cancel
                                        # contract.
                                        _f_was_cancelled = True
                                        _f_run_error_text = (
                                            "LLM cron cancelled mid-run "
                                            "(session reaper or operator action)"
                                        )
                                        raise
                                    except Exception as _llm_exc:
                                        _f_was_exception = True
                                        _f_run_error_text = str(_llm_exc)[:500]
                                        raise
                                    meta = getattr(result, "metadata", None)
                                    auth_required = False
                                    auth_link = None
                                    errors: list[str] = []
                                    if isinstance(meta, dict):
                                        auth_required = bool(meta.get("auth_required"))
                                        raw_link = meta.get("auth_link")
                                        auth_link = raw_link if isinstance(raw_link, str) and raw_link.strip() else None
                                        raw_errors = meta.get("errors")
                                        if isinstance(raw_errors, list):
                                            errors = [str(e) for e in raw_errors if str(e).strip()]

                                    if auth_required:
                                        record.status = "auth_required"
                                        # Preserve the link in output so the Web UI can display it without terminal access.
                                        if auth_link:
                                            record.output_preview = f"AUTH REQUIRED: {auth_link}"
                                        else:
                                            record.output_preview = "AUTH REQUIRED: open the Composio connect link shown in the run logs."
                                    elif errors:
                                        # Classification policy:
                                        #   errors + response_text  -> success_with_warnings (the agent
                                        #     produced a final answer despite tool/sandbox hiccups
                                        #     it recovered from; e.g. transient sandbox-violation
                                        #     attempts, retry-and-recover sequences). Logged as warning,
                                        #     not failed, so a clean cron tile reflects truth.
                                        #   errors + no response_text -> real error
                                        response_text = (getattr(result, "response_text", "") or "").strip()
                                        if response_text:
                                            record.status = "success"
                                            record.output_preview = response_text[:400]
                                            # Carry the warnings on the record so they're visible in the
                                            # Web UI's run detail and the activity_event metadata, even
                                            # though we don't fail the run.
                                            record.error = (
                                                f"completed with {len(errors)} tool warning(s); first: {errors[0][:200]}"
                                            )
                                            logger.warning(
                                                "Chron job %s succeeded with %d tool warning(s): %s",
                                                job.job_id,
                                                len(errors),
                                                errors[0][:200],
                                            )
                                        else:
                                            record.status = "error"
                                            record.error = errors[0]
                                            record.output_preview = record.error[:400]
                                    elif (
                                        _is_llm_deploy_kill_result(result)
                                        and _is_deploy_window_active()
                                    ):
                                        # Deploy-kill signature inside a deploy
                                        # window: the SDK's claude CLI subprocess
                                        # was SIGTERM'd (exit 143) mid-run and the
                                        # engine returned an empty result without
                                        # raising. Mirror the `!script` branch's
                                        # deploy-window handling: mark cancelled
                                        # (NOT completed — the work never
                                        # happened), advance next_run_at, and keep
                                        # the in-flight marker (the finally below
                                        # skips clearing for cancelled) so the
                                        # startup recovery pass requeues the slot
                                        # on next gateway boot.
                                        #
                                        # GUARDRAIL: the deploy-window predicate
                                        # is the ONLY thing that downgrades the
                                        # empty result here. Outside a deploy
                                        # window an empty result keeps its
                                        # pre-existing classification.
                                        _f_was_deploy_killed = True
                                        record.status = "cancelled"
                                        record.error = (
                                            "LLM run returned an empty result "
                                            "(no text, no tool calls) during "
                                            "deploy restart — claude CLI "
                                            "subprocess SIGTERM'd mid-run "
                                            "(will re-fire on next gateway boot)"
                                        )
                                        record.output_preview = record.error[:400]
                                        _f_run_error_text = record.error
                                        try:
                                            job.next_run_at = (
                                                time.time()
                                                + _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC
                                            )
                                            self.store.save_jobs(self.jobs.values())
                                            logger.info(
                                                "Chron job %s: LLM run showed the "
                                                "deploy-kill signature during a "
                                                "deploy window — marked cancelled, "
                                                "next_run_at advanced to +%ds",
                                                job.job_id,
                                                _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC,
                                            )
                                        except Exception as _llm_backfill_exc:  # noqa: BLE001
                                            logger.warning(
                                                "Chron job %s: failed to advance "
                                                "next_run_at after LLM deploy-"
                                                "cancellation: %s",
                                                job.job_id,
                                                _llm_backfill_exc,
                                            )
                                    else:
                                        record.status = "success"
                                        record.output_preview = (getattr(result, "response_text", "") or "")[:400]
                                    record.finished_at = time.time()
                                    self._persist_run_output(job, record, result)
                                    if record.status == "auth_required":
                                        _llm_failure_class = "auth_required"
                                    elif record.status == "cancelled":
                                        _llm_failure_class = "cancelled"
                                    else:
                                        _llm_failure_class = "cron_dispatch_failed"
                                    self._finalize_workflow_attempt(
                                        job=job,
                                        record=record,
                                        scheduled_at=scheduled_at,
                                        reason=reason,
                                        dispatch_key=dispatch_key,
                                        workflow_run_id=workflow_run_id,
                                        workflow_attempt_id=workflow_attempt_id,
                                        failure_reason=record.error or ("auth_required" if record.status == "auth_required" else None),
                                        failure_class=_llm_failure_class,
                                        retryable=record.status == "error",
                                    )
                                finally:
                                    # Phase F.1 / F.3 site-wiring CLOSE (LLM cron).
                                    # Runs on success, timeout, and exception
                                    # paths so every tick produces a
                                    # ``task_hub_runs`` row.  Mirrors the
                                    # !script branch's close logic but treats
                                    # the in-process LLM call as a synthetic
                                    # subprocess: rc=0 when the coroutine
                                    # returned, rc=1 when it raised or timed
                                    # out.  Best-effort throughout.
                                    if _f_task_id:
                                        try:
                                            from universal_agent import (
                                                task_hub as _f_th_llm,
                                            )
                                            from universal_agent.gateway_server import (
                                                _task_hub_open_conn as _f_open_conn_llm,
                                            )
                                            from universal_agent.services.cron_task_hub_link import (
                                                close_cron_task_link as _f_close_link_llm,
                                            )
                                            from universal_agent.services.worker_exit_classifier import (
                                                classify_worker_exit as _f_classify_llm,
                                                park_task_for_protocol_violation as _f_park_llm,
                                                task_was_closed_normally as _f_closed_llm,
                                            )
                                            _f_rc_equiv_llm = (
                                                0
                                                if not _f_was_timeout_killed
                                                and not _f_was_exception
                                                and not _f_was_cancelled
                                                and not _f_was_deploy_killed
                                                else 1
                                            )
                                            _f_conn_llm = _f_open_conn_llm()
                                            try:
                                                # Pre-close auto-linked cron
                                                # task on clean rc=0 paths so
                                                # F.3 doesn't treat the normal
                                                # cron lifecycle as a protocol
                                                # violation (mirrors !script).
                                                if (
                                                    _f_auto_linked
                                                    and _f_rc_equiv_llm == 0
                                                ):
                                                    try:
                                                        _f_conn_llm.execute(
                                                            "UPDATE task_hub_items "
                                                            "SET status = ?, seizure_state = ?, updated_at = ? "
                                                            "WHERE task_id = ?",
                                                            (
                                                                _f_th_llm.TASK_STATUS_COMPLETED,
                                                                "unseized",
                                                                _f_th_llm._now_iso(),
                                                                _f_task_id,
                                                            ),
                                                        )
                                                        _f_conn_llm.commit()
                                                    except Exception as _f_pre_close_exc_llm:
                                                        logger.debug(
                                                            "Phase F LLM auto-link pre-close skipped for cron job %s: %s",
                                                            job.job_id, _f_pre_close_exc_llm,
                                                        )

                                                _f_closed_normally_llm = _f_closed_llm(
                                                    _f_conn_llm, task_id=_f_task_id,
                                                )
                                                _f_classification_llm = _f_classify_llm(
                                                    return_code=_f_rc_equiv_llm,
                                                    was_signaled=False,
                                                    was_timeout_killed=_f_was_timeout_killed,
                                                    task_closed_normally=_f_closed_normally_llm,
                                                    was_cancelled=(
                                                        _f_was_cancelled
                                                        or _f_was_deploy_killed
                                                    ),
                                                )
                                                logger.info(
                                                    "Phase F.1 LLM cron job %s exit classified as %s "
                                                    "(task=%s, assignment=%s, rc_equiv=%s, auto_linked=%s)",
                                                    job.job_id, _f_classification_llm.outcome,
                                                    _f_task_id, _f_assignment_id or "<none>",
                                                    _f_rc_equiv_llm, _f_auto_linked,
                                                )
                                                if _f_assignment_id:
                                                    try:
                                                        _f_th_llm._close_run(
                                                            _f_conn_llm,
                                                            assignment_id=_f_assignment_id,
                                                            outcome=(
                                                                "completed"
                                                                if _f_rc_equiv_llm == 0
                                                                else "failed"
                                                            ),
                                                            summary=f"cron LLM {job.job_id}",
                                                            error=(_f_run_error_text or "")[:500],
                                                            metadata={
                                                                "worker_exit": _f_classification_llm.to_dict(),
                                                                "site": "cron",
                                                                "auto_linked": _f_auto_linked,
                                                                "execution_mode": "llm_in_process",
                                                            },
                                                        )
                                                        _f_conn_llm.execute(
                                                            "UPDATE task_hub_assignments "
                                                            "SET state = ?, ended_at = ? "
                                                            "WHERE assignment_id = ? AND ended_at IS NULL",
                                                            (
                                                                "completed" if _f_rc_equiv_llm == 0 else "failed",
                                                                _f_th_llm._now_iso(),
                                                                _f_assignment_id,
                                                            ),
                                                        )
                                                        _f_conn_llm.commit()
                                                    except Exception as _f_close_exc_llm:
                                                        logger.debug(
                                                            "Phase F.1 LLM _close_run skipped for cron job %s: %s",
                                                            job.job_id, _f_close_exc_llm,
                                                        )
                                                if _f_classification_llm.is_protocol_violation:
                                                    _f_park_llm(
                                                        _f_conn_llm,
                                                        task_id=_f_task_id,
                                                        site="cron",
                                                        summary=f"cron LLM {job.job_id} clean exit no disposition",
                                                        agent_id="cron_scheduler",
                                                    )
                                                # Operator-escalation hook: when the wall-clock
                                                # cap fires (timeout_killed) or the coroutine is
                                                # cancelled mid-run, leave a durable comment on
                                                # the cron's task row so the operator sees the
                                                # event in dashboard history instead of just an
                                                # innocent-looking refreshed timestamp in
                                                # NOT_ASSIGNED. Pairs with the
                                                # cron_consecutive_failures invariant — that
                                                # handles "this has been failing for N nights",
                                                # this handles "what happened on the most
                                                # recent run". TODO(operator-in-the-loop):
                                                # follow-up to push a needs_review prompt and
                                                # accept "keep working" / "abandon" replies.
                                                # Deploy-restart kills are
                                                # self-healing non-events (PR
                                                # #563 precedent) — the slot
                                                # requeues via the in-flight
                                                # marker, so skip the operator-
                                                # escalation comment for them.
                                                if _f_classification_llm.outcome in {
                                                    "timeout_killed",
                                                    "cancelled_mid_run",
                                                } and not _f_was_deploy_killed:
                                                    try:
                                                        _cap_s = request_metadata.get(
                                                            "turn_timeout_seconds"
                                                        )
                                                        _cap_label = (
                                                            f"{int(_cap_s)}s" if _cap_s else "configured cap"
                                                        )
                                                        _f_th_llm.add_comment(
                                                            _f_conn_llm,
                                                            task_id=_f_task_id,
                                                            author="cron_scheduler",
                                                            content=(
                                                                f"Cron LLM exceeded {_cap_label} "
                                                                f"(outcome={_f_classification_llm.outcome}). "
                                                                "Workflow paused mid-run. If this is a "
                                                                "slow-but-healthy pipeline, raise "
                                                                "`timeout_seconds` on the cron config; "
                                                                "if it looks wedged, investigate the run "
                                                                "workspace before the next scheduled fire."
                                                            ),
                                                        )
                                                    except Exception as _f_comment_exc_llm:
                                                        logger.debug(
                                                            "Phase F.1 LLM operator-escalation comment skipped for cron job %s: %s",
                                                            job.job_id, _f_comment_exc_llm,
                                                        )
                                                if _f_auto_linked:
                                                    _f_close_link_llm(
                                                        _f_conn_llm,
                                                        task_id=_f_task_id,
                                                        success=(_f_rc_equiv_llm == 0),
                                                    )
                                                # Artifact-disclosure rail. Fires only on
                                                # clean_exit_zero AND when the cron has
                                                # ``metadata.notify_on_artifact`` opted in.
                                                #
                                                # AWAIT inline (not fire-and-forget). The
                                                # 2026-05-24 verification showed that
                                                # ``asyncio.get_running_loop().create_task(...)``
                                                # without retaining the task reference got
                                                # garbage-collected when the cron coroutine
                                                # broke out of its loop. The notifier itself
                                                # swallows every exception path internally,
                                                # so awaiting it never raises — at worst we
                                                # add a few seconds to the close-out, which
                                                # is fine because this is the cron's last
                                                # step before the heartbeat-wake. Logs at
                                                # INFO/WARNING so verification is
                                                # observable in journalctl.
                                                if (
                                                    _f_rc_equiv_llm == 0
                                                    and bool((job.metadata or {}).get("notify_on_artifact"))
                                                ):
                                                    logger.info(
                                                        "Phase F.1 cron_artifact_notifier triggered for job %s (opted in)",
                                                        job.job_id,
                                                    )
                                                    try:
                                                        import sys as _f_sys_llm

                                                        from universal_agent import (
                                                            gateway_server as _f_gw_llm,
                                                        )
                                                        from universal_agent.services.cron_artifact_notifier import (
                                                            notify_cron_artifact as _f_notify_llm,
                                                        )
                                                        # Python ``__main__`` vs imported-module gotcha:
                                                        # the gateway runs as ``python -m universal_agent.gateway_server``,
                                                        # so ``sys.modules['__main__']`` is the live module where
                                                        # ``lifespan`` startup mutated ``_agentmail_service`` to the
                                                        # AgentMailService instance. A plain ``from universal_agent
                                                        # import gateway_server`` returns a SEPARATE copy under the
                                                        # qualified name whose ``_agentmail_service`` is the pristine
                                                        # None from the file-level declaration. Look up __main__
                                                        # first; fall back to the imported module for safety in
                                                        # non-main-script contexts (uvicorn programmatic boot,
                                                        # tests, etc.).
                                                        _f_main_mod_llm = _f_sys_llm.modules.get("__main__")
                                                        _f_mail_svc_llm = getattr(_f_main_mod_llm, "_agentmail_service", None)
                                                        if _f_mail_svc_llm is None:
                                                            _f_mail_svc_llm = getattr(_f_gw_llm, "_agentmail_service", None)
                                                        if _f_mail_svc_llm is None:
                                                            logger.warning(
                                                                "Phase F.1 cron_artifact_notifier SKIPPED for %s: AgentMail service not initialized in __main__ or imported module",
                                                                job.job_id,
                                                            )
                                                        else:
                                                            # Same recipient resolver also lives in both module copies;
                                                            # prefer __main__ so we read live env state instead of an
                                                            # import-time snapshot.
                                                            _f_recipient_fn_llm = (
                                                                getattr(_f_main_mod_llm, "_proactive_review_recipient", None)
                                                                or _f_gw_llm._proactive_review_recipient
                                                            )
                                                            _f_recipient_llm = _f_recipient_fn_llm("")
                                                            _f_dashboard_base_llm = (
                                                                os.getenv("FRONTEND_URL", "")
                                                                or os.getenv("UA_PUBLIC_BASE_URL", "")
                                                                or "https://app.clearspringcg.com"
                                                            )
                                                            _f_notify_result_llm = await _f_notify_llm(
                                                                conn=_f_conn_llm,
                                                                mail_service=_f_mail_svc_llm,
                                                                job_id=job.job_id,
                                                                job_metadata=job.metadata or {},
                                                                job_command=job.command or "",
                                                                workspace_dir=Path(job.workspace_dir)
                                                                if job.workspace_dir
                                                                else Path("/tmp"),
                                                                started_at=float(record.started_at or time.time()),
                                                                finished_at=float(record.finished_at or time.time()),
                                                                recipient=_f_recipient_llm,
                                                                dashboard_base_url=_f_dashboard_base_llm,
                                                            )
                                                            if _f_notify_result_llm:
                                                                logger.info(
                                                                    "Phase F.1 cron_artifact_notifier delivered artifact %s for job %s",
                                                                    _f_notify_result_llm.get("artifact_id"),
                                                                    job.job_id,
                                                                )
                                                            else:
                                                                logger.warning(
                                                                    "Phase F.1 cron_artifact_notifier returned None for job %s (no artifacts or opt-out)",
                                                                    job.job_id,
                                                                )
                                                    except Exception as _f_notify_exc_llm:
                                                        logger.warning(
                                                            "Phase F.1 cron_artifact_notifier RAISED for job %s: %s",
                                                            job.job_id, _f_notify_exc_llm,
                                                            exc_info=True,
                                                        )
                                            finally:
                                                _f_conn_llm.close()
                                        except Exception as _f_exc_llm:
                                            logger.debug(
                                                "Phase F.1/F.3 LLM wiring skipped for cron job %s: %s",
                                                job.job_id, _f_exc_llm,
                                            )
                                break
                            record.finished_at = time.time()
                            self._persist_run_output(job, record, result)
                            break
                        except Exception as exc:
                            is_locked = "database is locked" in str(exc).lower()
                            if is_locked and attempt < max_db_lock_retries:
                                attempt += 1
                                delay_seconds = _CRON_DB_LOCK_RETRY_DELAY_SECONDS * attempt
                                logger.warning(
                                    "Chron job %s encountered database lock (attempt %d/%d); retrying in %.1fs",
                                    job.job_id,
                                    attempt,
                                    max_db_lock_retries,
                                    delay_seconds,
                                )
                                await asyncio.sleep(delay_seconds)
                                continue
                            raise
            except asyncio.CancelledError:
                # Service shutdown / deploy restart cancels in-flight cron
                # tasks. Without this branch, CancelledError (BaseException
                # subclass in Py3.8+) bypasses the generic Exception handler
                # below — the run was never finalized, and on next startup
                # the recovery sweep emits a phantom "Cron Run Failed". Mark
                # explicitly cancelled and re-raise so cancellation completes.
                record.status = "cancelled"
                record.finished_at = time.time()
                record.error = "cancelled (likely service restart)"
                logger.info("Chron job %s cancelled mid-run", job.job_id)
                self._finalize_workflow_attempt(
                    job=job,
                    record=record,
                    scheduled_at=scheduled_at,
                    reason=reason,
                    dispatch_key=dispatch_key,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                    failure_reason=record.error,
                    failure_class="cancelled",
                    retryable=False,
                )
                raise
            except asyncio.TimeoutError:
                record.status = "error"
                record.finished_at = time.time()
                timeout_label = timeout_seconds if timeout_seconds is not None else "configured"
                record.error = f"execution timed out after {timeout_label}s"
                logger.error("Chron job %s timed out after %ss", job.job_id, timeout_label)
                self._write_timeout_crash_report(
                    job,
                    record,
                    timeout_seconds=timeout_seconds,
                    source="cron_service",
                )
                self._finalize_workflow_attempt(
                    job=job,
                    record=record,
                    scheduled_at=scheduled_at,
                    reason=reason,
                    dispatch_key=dispatch_key,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                    failure_reason=record.error,
                    failure_class="execution_timeout",
                    retryable=True,
                )
            except Exception as exc:
                record.status = "error"
                record.finished_at = time.time()
                record.error = str(exc)
                logger.error("Chron job %s failed: %s", job.job_id, exc)
                # Defer rate-limit failures to the next scheduled tick
                # instead of retrying immediately — see
                # `_is_rate_limit_exception` docblock for the 2026-05-23
                # incident shape that motivated this.
                _retryable = not _is_rate_limit_exception(record.error)
                _failure_class = (
                    "rate_limited" if not _retryable else "cron_dispatch_failed"
                )
                if not _retryable:
                    logger.warning(
                        "Chron job %s hit upstream rate-limit signature; "
                        "deferring to next scheduled tick (no retry).",
                        job.job_id,
                    )
                self._finalize_workflow_attempt(
                    job=job,
                    record=record,
                    scheduled_at=scheduled_at,
                    reason=reason,
                    dispatch_key=dispatch_key,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                    failure_reason=record.error,
                    failure_class=_failure_class,
                    retryable=_retryable,
                )
            finally:
                retry_scheduled = record.status == "retry_queued"
                if not retry_scheduled:
                    self.running_jobs.discard(job.job_id)
                    self.running_job_scheduled_at.pop(job.job_id, None)
                # Phase F close of the in-flight marker. Deploy-restart
                # cancellations (and pending retries, which are still the
                # same logical slot) keep their marker so the startup
                # recovery pass requeues the interrupted slot on next boot.
                if record.status not in {"cancelled", "retry_queued"}:
                    self._clear_inflight(job.job_id)
                moved_outputs = self._organize_workspace_outputs(job.workspace_dir)
                # Finalize one-shot schedule consumption only after run actually started.
                if reason == "schedule" and job.run_at is not None:
                    job.last_run_at = record.started_at or time.time()
                    job.schedule_next(job.last_run_at)
                    self.store.save_jobs(self.jobs.values())
                self.store.append_run(record)
                self._emit_event({"type": "cron_run_completed", "run": record.to_dict(), "reason": reason})

                # Note: We intentionally do NOT explicitly close the gateway session here.
                # Cron runs are assigned TTL classes (admin TTL default 10 minutes).
                # Leaving them in memory allows the user to click "Open" in the UI
                # and attach to the session (rehydrate) to view the transcript and logs.
                # The gateway's session reaper will automatically clean up the session
                # once its inactivity TTL expires.
                pass

                # Post-run memory capture: write a session rollover to shared memory
                # so cron run context is available to future sessions (fix #6).
                if record.status == "success":
                    try:
                        from universal_agent.feature_flags import memory_enabled
                        if memory_enabled():
                            from universal_agent.memory.orchestrator import (
                                get_memory_orchestrator,
                            )
                            from universal_agent.memory.paths import (
                                resolve_shared_memory_workspace,
                            )
                            _ws_dir = str(job.workspace_dir)
                            _transcript = os.path.join(_ws_dir, "transcript.md")
                            _shared_root = resolve_shared_memory_workspace(_ws_dir)
                            _broker = get_memory_orchestrator(workspace_dir=_shared_root)
                            _broker.capture_session_rollover(
                                session_id=record.session_id or job.job_id,
                                trigger="cron_run_completed",
                                transcript_path=_transcript,
                                summary=(
                                    f"Cron job '{job.job_id}' completed. "
                                    + (record.output_preview or "")[:200]
                                ),
                            )
                    except Exception:
                        pass
                if moved_outputs:
                    logger.info(
                        "Chron job %s moved %d root output(s) into work_products: %s",
                        job.job_id,
                        len(moved_outputs),
                        ", ".join(moved_outputs[:5]),
                    )
                
                # Enqueue system event for target session (if wake_heartbeat configured)
                metadata = job.metadata or {}
                target_session = metadata.get("target_session") or metadata.get("session_id")
                if target_session:
                    event_text = f"Chron job '{job.command[:50]}...' completed with status={record.status}"
                    if record.error:
                        event_text += f", error: {record.error[:100]}"
                    elif record.output_preview:
                        event_text += f", output: {record.output_preview[:100]}..."
                    self._emit_system_event(target_session, {
                        "type": "cron_completed",
                        "job_id": job.job_id,
                        "status": record.status,
                        "output_preview": record.output_preview,
                        "error": record.error,
                        "text": event_text,
                    })
                
                self._maybe_wake_heartbeat(job, reason)
                
                # One-shot: delete job after successful run if configured
                if job.delete_after_run and record.status == "success":
                    logger.info("Deleting one-shot chron job %s after successful run", job.job_id)
                    self.delete_job(job.job_id)

                # ── Task Hub lifecycle closure ──
                # When a cron job sourced from the email scheduler completes,
                # mark the originating Task Hub item as "completed" so it
                # moves out of `in_progress`/`scheduled` into the done column.
                if record.status == "success" and metadata.get("source") == "email_task_scheduler":
                    _task_id = metadata.get("task_id")
                    if _task_id:
                        try:
                            from universal_agent import task_hub as _th
                            from universal_agent.gateway_server import (
                                _task_hub_open_conn,
                            )
                            _conn = _task_hub_open_conn()
                            try:
                                _th.perform_task_action(
                                    _conn,
                                    task_id=_task_id,
                                    action="complete",
                                    reason=f"Cron job {job.job_id} completed successfully",
                                    agent_id="cron_scheduler",
                                )
                                _conn.commit()
                                logger.info(
                                    "📧✅ Task Hub item %s marked completed after cron job %s",
                                    _task_id, job.job_id,
                                )
                            finally:
                                _conn.close()
                        except Exception as _th_exc:
                            logger.warning(
                                "📧⚠️  Failed to mark Task Hub item %s as completed: %s",
                                _task_id, _th_exc,
                            )

            return record

    def _organize_workspace_outputs(self, workspace_dir: str) -> list[str]:
        """Move common deliverables from workspace root into work_products."""
        try:
            workspace = Path(workspace_dir).resolve()
            if not workspace.exists():
                return []

            work_products_dir = workspace / "work_products"
            media_dir = work_products_dir / "media"
            work_products_dir.mkdir(parents=True, exist_ok=True)
            media_dir.mkdir(parents=True, exist_ok=True)

            moved: list[str] = []
            for entry in workspace.iterdir():
                if not entry.is_file():
                    continue
                if entry.name.startswith("."):
                    continue
                if entry.name in _CRON_WORKSPACE_KEEP_FILES:
                    continue
                suffix = entry.suffix.lower()
                if suffix in _CRON_MEDIA_EXTENSIONS:
                    target = self._dedupe_destination(media_dir / entry.name)
                elif suffix in _CRON_WORK_PRODUCT_EXTENSIONS:
                    target = self._dedupe_destination(work_products_dir / entry.name)
                else:
                    continue
                shutil.move(str(entry), str(target))
                moved.append(str(target.relative_to(workspace)))
            return moved
        except Exception as exc:
            logger.warning("Failed organizing chron workspace outputs for %s: %s", workspace_dir, exc)
            return []

    def _persist_run_output(self, job: CronJob, record: CronRunRecord, result: Any) -> None:
        """Save cron run outputs into work_products and optional artifacts directory."""
        response_text = (getattr(result, "response_text", "") or "").strip()
        if not response_text:
            return
        try:
            workspace = Path(job.workspace_dir).resolve()
            work_products_dir = workspace / "work_products"
            work_products_dir.mkdir(parents=True, exist_ok=True)
            target = self._dedupe_destination(work_products_dir / _CRON_OUTPUT_FILENAME)
            content = (
                f"# Chron Output\n\n"
                f"- Job ID: {job.job_id}\n"
                f"- Run ID: {record.run_id}\n"
                f"- Status: {record.status}\n"
                f"- Finished At: {datetime.fromtimestamp(record.finished_at or time.time()).isoformat()}\n\n"
                f"## Response\n\n{response_text}\n"
            )
            target.write_text(content, encoding="utf-8")

            artifacts_root = os.getenv("UA_ARTIFACTS_DIR")
            if not artifacts_root:
                artifacts_root = str(Path(__file__).resolve().parent.parent.parent / "artifacts")
            if artifacts_root:
                artifacts_dir = Path(artifacts_root).resolve() / "cron" / job.job_id
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                artifacts_target = self._dedupe_destination(artifacts_dir / target.name)
                artifacts_target.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to persist chron output for %s: %s", job.job_id, exc)

    def _write_timeout_crash_report(
        self,
        job: CronJob,
        record: CronRunRecord,
        *,
        timeout_seconds: Optional[int],
        source: str,
    ) -> None:
        try:
            workspace = Path(job.workspace_dir).resolve()
            crash_file = workspace / "work_products" / "daemon_timeout_crash.json"
            payload = {
                "error": record.error or "Cron job execution timed out",
                "reason": "cron_execution_timeout",
                "source": source,
                "job_id": job.job_id,
                "run_id": record.run_id,
                "workflow_run_id": record.workflow_run_id,
                "workflow_attempt_id": record.workflow_attempt_id,
                "workspace_dir": str(workspace),
                "crash_report_path": str(crash_file),
                "timeout_threshold": timeout_seconds,
                "timeout_threshold_seconds": timeout_seconds,
                "command": job.command,
                "output_preview": record.output_preview,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            crash_file.parent.mkdir(parents=True, exist_ok=True)
            crash_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write chron timeout crash report for %s: %s", job.job_id, exc)

    def _dedupe_destination(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        for index in range(2, 1000):
            candidate = parent / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                return candidate
        return parent / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

    def _resolve_job_timeout_seconds(self, job: CronJob) -> Optional[int]:
        if job.timeout_seconds is not None:
            return _normalize_timeout_seconds(job.timeout_seconds)
        metadata = job.metadata or {}
        return _normalize_timeout_seconds(metadata.get("timeout_seconds"))

    def _emit_event(self, payload: dict[str, Any]) -> None:
        if not self.event_sink:
            return
        try:
            self.event_sink(payload)
        except Exception as exc:
            logger.warning("Chron event sink failed: %s", exc)

    def _emit_cron_success_intelligence(self, job: "CronJob", record: "CronRunRecord") -> None:
        """Write an intelligence-grade activity event for a successful
        cron run so Mission Control tier-1 can discover "this useful
        thing just happened" cards.

        Most operator-relevant cron jobs are not noisy maintenance — they
        are reconciliations, digests, reports, and ingestion runs that
        produce real artifacts. Without this hook, those wins are
        invisible to the dashboard. We pull the run's duration and any
        artifact references off the record so the LLM has enough to
        synthesize a useful narrative.

        Heartbeat-style cron jobs (frequent, low-signal) opt OUT via
        a metadata flag `mission_control_silent: true` so we don't
        flood the dashboard.
        """
        metadata = job.metadata or {}
        if metadata.get("mission_control_silent") is True:
            return  # noisy job opted out of dashboard surfacing

        from universal_agent.services.intelligence_emitter import (
            SEVERITY_SUCCESS,
            emit_intelligence_event,
        )

        duration_s: float | None = None
        try:
            if record.started_at and record.finished_at:
                duration_s = max(0.0, float(record.finished_at) - float(record.started_at))
        except Exception:
            duration_s = None

        artifact_path = ""
        try:
            artifact_path = str(record.output_path or "") or ""
        except Exception:
            artifact_path = ""

        title = f"Cron `{job.job_id}` completed"
        summary_bits = [f"Job `{job.job_id}` succeeded"]
        if duration_s is not None:
            summary_bits.append(f"in {duration_s:.1f}s")
        if artifact_path:
            summary_bits.append(f"→ {artifact_path}")
        summary = " ".join(summary_bits) + "."

        emit_intelligence_event(
            source_domain="cron",
            kind="cron_job_success",
            title=title,
            summary=summary,
            severity=SEVERITY_SUCCESS,
            metadata={
                "job_id": job.job_id,
                "duration_s": duration_s,
                "artifact_path": artifact_path,
                "scheduled_at": getattr(record, "scheduled_at", None),
                "started_at": getattr(record, "started_at", None),
                "finished_at": getattr(record, "finished_at", None),
                "mode": metadata.get("mode") or "cron",
            },
        )

    def _emit_system_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Enqueue a system event for the given session (surfaced in next heartbeat)."""
        if not self.system_event_callback:
            return
        try:
            self.system_event_callback(session_id, event)
        except Exception as exc:
            logger.warning("Chron system event callback failed: %s", exc)

    def _maybe_wake_heartbeat(self, job: CronJob, reason: str) -> None:
        if not self.wake_callback:
            return
        metadata = job.metadata or {}
        trigger = metadata.get("wake_heartbeat") or metadata.get("heartbeat_wake")
        session_id = (
            metadata.get("wake_session_id")
            or metadata.get("session_id")
            or metadata.get("target_session_id")
            or metadata.get("target_session")
        )
        if not session_id:
            return
        mode = "next"
        if isinstance(trigger, bool):
            if not trigger:
                return
            mode = str(metadata.get("wake_mode") or mode)
        elif isinstance(trigger, str):
            trigger_mode = trigger.strip().lower()
            if trigger_mode in {"", "0", "false", "off", "none", "disable", "disabled"}:
                return
            if trigger_mode in {"auto", "default"}:
                mode = "next"
            else:
                mode = trigger_mode
        elif trigger is None:
            # For session-bound cron jobs, wake next heartbeat by default so due-work
            # transitions reliably surface in the target session without extra polling.
            mode = str(metadata.get("wake_mode") or mode)
        else:
            mode = str(metadata.get("wake_mode") or mode)
        mode = mode.strip().lower()
        if mode not in {"now", "next"}:
            mode = "next"
        # M4 selective coupling — close the session-bound back door. An
        # AUTONOMOUS system cron's "next"-mode wake is the same redundant
        # cron→heartbeat coupling that the gateway path
        # (_maybe_wake_heartbeat_after_autonomous_cron) now default-denies, so
        # apply the identical default-deny allowlist here. Non-autonomous
        # (user/email-scheduled) session wakes and explicit wake_mode="now"
        # urgent wakes are deliberately left intact — those are the "different
        # path" the north-star keeps responsive.
        if (
            mode == "next"
            and bool(metadata.get("autonomous"))
            and coupling_wake_selective_enabled()
            and str(metadata.get("system_job") or "").strip()
            not in coupling_wake_allowed_jobs()
        ):
            return
        try:
            self.wake_callback(session_id, mode, f"cron:{job.job_id}:{reason}")
        except Exception as exc:
            logger.warning("Chron heartbeat wake failed for %s: %s", job.job_id, exc)
