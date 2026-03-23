
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
    "If nothing needs attention, reply HEARTBEAT_OK."
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
    int(os.getenv("UA_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE", "1") or 1),
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
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            last_run=data.get("last_run", 0.0),
            last_message_hash=data.get("last_message_hash"),
            last_message_ts=data.get("last_message_ts", 0.0),
            last_summary=data.get("last_summary"),
            retry_attempt=int(data.get("retry_attempt", 0) or 0),
            next_retry_at=float(data.get("next_retry_at", 0.0) or 0.0),
            retry_reason=data.get("retry_reason"),
            retry_kind=data.get("retry_kind"),
            last_retry_delay_seconds=float(data.get("last_retry_delay_seconds", 0.0) or 0.0),
        )


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


def _compose_heartbeat_prompt(
    base_prompt: str,
    *,
    investigation_only: bool,
    task_hub_claims: list[dict[str, Any]],
) -> str:
    prompt = (base_prompt or DEFAULT_HEARTBEAT_PROMPT).strip()
    if "{ok_token}" in prompt:
        # Placeholder replacement happens separately where schedule.ok_tokens is available.
        pass
    if investigation_only and "investigation-only mode" not in prompt.lower():
        prompt = f"{prompt} {INVESTIGATION_ONLY_PROMPT_INSTRUCTIONS}".strip()
    if task_hub_claims:
        task_ids = sorted(
            {
                str(item.get("task_id") or "").strip()
                for item in task_hub_claims
                if str(item.get("task_id") or "").strip()
            }
        )
        prompt = (
            f"{prompt}\n\n"
            "Task Hub lifecycle requirement: before finishing, disposition every claimed Task Hub item using "
            "the `task_hub_task_action` tool with one of: `review`, `complete`, `block`, `park`, `unblock`. "
            "Do not leave claimed tasks in `in_progress`. If work is not completed, use `review`.\n"
            f"Claimed task_ids: {', '.join(task_ids) if task_ids else '(none)'}"
        )
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
    ):
        skip_reason = "no_actionable_work"

    return {
        "autonomous_enabled": autonomous_enabled,
        "max_actionable": max_actionable,
        "max_system_events": max_system_events,
        "max_proactive_per_cycle": max_proactive_per_cycle,
        "skip_reason": skip_reason,
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

    async def _process_session(self, session: GatewaySession):
        """Check if a session needs a heartbeat run."""
        # Check for idle cleanup first
        if self._check_session_idle(session):
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
        
        # Seed HEARTBEAT.md if missing
        if not hb_file.exists() and GLOBAL_HEARTBEAT_PATH.exists():
            try:
                shutil.copy(GLOBAL_HEARTBEAT_PATH, hb_file)
                logger.info("Seeded %s from global memory for session %s", HEARTBEAT_FILE, session.session_id)
            except Exception as e:
                logger.warning("Failed to seed HEARTBEAT.md for %s: %s", session.session_id, e)

        # Some agent/tooling conventions look for memory files under
        # <workspace>/memory/. Seed there too (without overwriting) so heartbeat
        # runs don't fail if the model chooses that path.
        try:
            mem_dir = workspace / "memory"
            mem_dir.mkdir(exist_ok=True)
            mem_hb_file = mem_dir / HEARTBEAT_FILE
            if not mem_hb_file.exists():
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
            todoist_actionable_count = 0
            todoist_brainstorm_candidates: list[dict[str, Any]] = []
            todoist_timeout_seconds = max(
                0.1,
                float(os.getenv("UA_HEARTBEAT_TODOIST_TIMEOUT_SECONDS", "1.5") or 1.5),
            )
            todoist_brainstorm_limit = max(
                1,
                _parse_int(
                    os.getenv("UA_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE"),
                    DEFAULT_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE,
                ),
            )
            try:
                from universal_agent.services.todoist_service import TodoService

                def _collect_todoist_heartbeat_payload() -> tuple[int, list[dict[str, Any]], dict[str, Any] | None]:
                    todo_service = TodoService()
                    todoist_summary_payload: dict[str, Any] | None = None
                    actionable_count = 0
                    summary_result = todo_service.heartbeat_summary()
                    if isinstance(summary_result, dict):
                        actionable_count = max(0, int(summary_result.get("actionable_count") or 0))
                        if actionable_count > 0:
                            todoist_summary_payload = summary_result

                    candidate_rows: list[dict[str, Any]] = []
                    brainstorm_method = getattr(todo_service, "heartbeat_brainstorm_candidates", None)
                    if callable(brainstorm_method):
                        raw_candidates = brainstorm_method(limit=todoist_brainstorm_limit)
                        if isinstance(raw_candidates, list):
                            candidate_rows = [item for item in raw_candidates if isinstance(item, dict)]
                    return actionable_count, candidate_rows, todoist_summary_payload

                (
                    todoist_actionable_count,
                    todoist_brainstorm_candidates,
                    todoist_summary_payload,
                ) = await asyncio.wait_for(
                    asyncio.to_thread(_collect_todoist_heartbeat_payload),
                    timeout=todoist_timeout_seconds,
                )
                if todoist_summary_payload is not None:
                    metadata["todoist_summary"] = todoist_summary_payload
                if todoist_brainstorm_candidates:
                    metadata["todoist_brainstorm_candidates"] = todoist_brainstorm_candidates
            except asyncio.TimeoutError:
                logger.debug(
                    "Todoist heartbeat injection timed out for %s after %.1fs",
                    session.session_id,
                    todoist_timeout_seconds,
                )
            except Exception as exc:
                logger.debug("Todoist heartbeat injection unavailable for %s: %s", session.session_id, exc)
            guard_policy = _heartbeat_guard_policy(
                actionable_count=None,
                brainstorm_candidate_count=len(todoist_brainstorm_candidates),
                system_event_count=len(system_events),
                has_exec_completion=has_exec_completion,
                has_heartbeat_content=bool(heartbeat_content.strip()),
            )
            max_proactive_per_cycle = int(guard_policy.get("max_proactive_per_cycle") or 1)
            max_system_events = int(guard_policy.get("max_system_events") or 1)

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
                    queue = task_hub.get_dispatch_queue(conn, limit=max(3, max_proactive_per_cycle * 4))
                    dispatch_actionable_count = int(queue.get("eligible_total") or 0)
                    task_hub_claimed = task_hub.claim_next_dispatch_tasks(
                        conn,
                        limit=max(1, max_proactive_per_cycle),
                        agent_id=task_hub_agent_id,
                    )
                    dispatch_claimed_count = len(task_hub_claimed)
                    task_hub_claimed_count = dispatch_claimed_count
                    should_schedule_continuation = dispatch_claimed_count > 0
                    if should_schedule_continuation:
                        continuation_reason = "task_hub_followup"

                    # Enhancement 1: Escalation Pre-Check — enrich each claimed task
                    # with past escalation resolutions so the agent doesn't repeat mistakes.
                    if task_hub_claimed:
                        try:
                            from universal_agent.services.todoist_service import TodoService

                            for claimed in task_hub_claimed:
                                title = str(claimed.get("title") or "").strip()
                                if title:
                                    resolutions = TodoService.check_escalation_memory(
                                        title, db_conn=conn, limit=2,
                                    )
                                    if resolutions:
                                        claimed["escalation_history"] = resolutions
                                        logger.debug(
                                            "Enriched claimed task %s with %d escalation resolutions",
                                            claimed.get("task_id"), len(resolutions),
                                        )
                        except Exception:
                            pass  # escalation memory is advisory

                    if task_hub_claimed:
                        hub_event = {
                            "type": "task_hub_dispatch",
                            "payload": {
                                "queue_build_id": str(queue.get("queue_build_id") or ""),
                                "eligible_total": int(queue.get("eligible_total") or 0),
                                "claimed_count": len(task_hub_claimed),
                                "claimed": task_hub_claimed,
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

                finally:
                    conn.close()
            except Exception as exc:
                logger.info("Task Hub heartbeat pre-step unavailable for %s: %s", session.session_id, exc)

            if len(system_events) > max_system_events:
                system_events = system_events[-max_system_events:]
                metadata["system_events"] = system_events

            guard_policy = _heartbeat_guard_policy(
                actionable_count=int(dispatch_actionable_count or 0) + int(todoist_actionable_count or 0),
                brainstorm_candidate_count=int(dispatch_claimed_count or 0) + len(todoist_brainstorm_candidates),
                system_event_count=len(system_events),
                has_exec_completion=has_exec_completion,
                has_heartbeat_content=bool(heartbeat_content.strip()),
            )
            guard_skip_reason = str(guard_policy.get("skip_reason") or "").strip()
            metadata["heartbeat_guard"] = {
                "autonomous_enabled": bool(guard_policy.get("autonomous_enabled")),
                "max_actionable": int(guard_policy.get("max_actionable") or DEFAULT_HEARTBEAT_MAX_ACTIONABLE),
                "max_system_events": int(guard_policy.get("max_system_events") or DEFAULT_HEARTBEAT_MAX_SYSTEM_EVENTS),
                "max_proactive_per_cycle": int(
                    guard_policy.get("max_proactive_per_cycle") or DEFAULT_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE
                ),
                "actionable_count": int(dispatch_actionable_count or 0) + int(todoist_actionable_count or 0),
                "brainstorm_candidate_count": int(dispatch_claimed_count or 0) + len(todoist_brainstorm_candidates),
                "system_event_count": len(system_events),
                "skip_reason": guard_skip_reason or None,
            }

            # Compose heartbeat prompt only after Task Hub claims are known so the
            # model can explicitly disposition claimed items before completion.
            if has_exec_completion:
                base_prompt = EXEC_EVENT_PROMPT
                logger.info("Using EXEC_EVENT_PROMPT for session %s (exec completion detected)", session.session_id)
            else:
                base_prompt = schedule.prompt.strip() or DEFAULT_HEARTBEAT_PROMPT
                if "{ok_token}" in base_prompt:
                    ok_token = schedule.ok_tokens[0] if schedule.ok_tokens else DEFAULT_OK_TOKENS[0]
                    base_prompt = base_prompt.replace("{ok_token}", ok_token)
            prompt = _compose_heartbeat_prompt(
                base_prompt,
                investigation_only=heartbeat_investigation_only,
                task_hub_claims=task_hub_claimed,
            )
            
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
            if _findings_written:
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

            if not _findings_written and not ok_only and not should_skip_agent_run:
                try:
                    _wp_dir = Path(session.workspace_dir) / "work_products"
                    _wp_dir.mkdir(parents=True, exist_ok=True)
                    _synthetic = {
                        "version": 1,
                        "overall_status": "warn" if not run_failed else "critical",
                        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                        "source": "heartbeat_synthetic",
                        "summary": (
                            f"Heartbeat completed with activity but the agent did not "
                            f"write structured findings. Response preview: "
                            f"{(response_text or full_response)[:200]}"
                        ),
                        "findings": [
                            {
                                "finding_id": "synthetic_missing_findings_artifact",
                                "category": "gateway",
                                "severity": "warn" if not run_failed else "critical",
                                "metric_key": "heartbeat_findings_artifact_written",
                                "observed_value": False,
                                "threshold_text": "agent should write findings JSON",
                                "known_rule_match": False,
                                "confidence": "medium",
                                "title": "Synthetic Findings (Agent Omitted Artifact)",
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
                        ],
                    }
                    _synthetic_path = _wp_dir / _findings_filename
                    _synthetic_path.write_text(
                        json.dumps(_synthetic, indent=2, default=str),
                        encoding="utf-8",
                    )
                    work_product_paths.append(str(_synthetic_path))
                    logger.info(
                        "Wrote synthetic heartbeat findings for %s → %s",
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
