
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Callable

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.gateway import InProcessGateway, GatewaySession, GatewayRequest
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
    "Investigation-only mode: do not modify repository source files or run mutating shell commands. "
    "If you draft code, write artifacts under work_products/ or UA_ARTIFACTS_DIR only. "
    "If nothing needs attention, reply HEARTBEAT_OK."
)
DEFAULT_INTERVAL_SECONDS = 30 * 60  # 30 minutes default
MIN_INTERVAL_SECONDS = max(
    1,
    int(os.getenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", str(30 * 60)) or str(30 * 60)),
)  # Never run heartbeats more frequently than this minimum
BUSY_RETRY_DELAY = 10  # Seconds
DEFAULT_HEARTBEAT_EXEC_TIMEOUT = 300
MIN_HEARTBEAT_EXEC_TIMEOUT = 300
DEFAULT_ACK_MAX_CHARS = 300
DEFAULT_OK_TOKENS = ["HEARTBEAT_OK", "UA_HEARTBEAT_OK"]
DEFAULT_FOREGROUND_COOLDOWN_SECONDS = max(
    0,
    int(os.getenv("UA_HEARTBEAT_FOREGROUND_COOLDOWN_SECONDS", "1800") or 1800),
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
    
    def to_dict(self):
        return {
            "last_run": self.last_run,
            "last_message_hash": self.last_message_hash,
            "last_message_ts": self.last_message_ts,
            "last_summary": self.last_summary,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            last_run=data.get("last_run", 0.0),
            last_message_hash=data.get("last_message_hash"),
            last_message_ts=data.get("last_message_ts", 0.0),
            last_summary=data.get("last_summary"),
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

class HeartbeatService:
    def __init__(
        self,
        gateway: InProcessGateway,
        connection_manager,
        system_event_provider: Optional[SystemEventProvider] = None,
        event_sink: Optional[HeartbeatEventSink] = None,
    ):
        self.gateway = gateway
        self.connection_manager = connection_manager
        self.system_event_provider = system_event_provider
        self.event_sink = event_sink
        self.execution_timeout_seconds = _resolve_exec_timeout_seconds()
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

        interval_raw = os.getenv("UA_HEARTBEAT_EVERY") or os.getenv("UA_HEARTBEAT_INTERVAL")
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
        schedule.every_seconds = max(MIN_INTERVAL_SECONDS, int(schedule.every_seconds or DEFAULT_INTERVAL_SECONDS))

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
            
            # Default 5 minutes (300s)
            idle_timeout = int(os.getenv("UA_HEARTBEAT_IDLE_TIMEOUT", "300"))
            
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
        now = time.time()

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
                 with open(state_path, "w") as f:
                     json.dump(state.to_dict(), f)
             except Exception:
                 pass

        # Required scheduling behavior: missed windows are not backfilled.
        # If heartbeat is locked (busy or foreground lock), consume scheduled windows.
        # Do not consume explicit wake requests.
        lock_reason = self._session_heartbeat_lock_reason(session, now)
        if lock_reason:
            if session.session_id in self.wake_sessions or session.session_id in self.wake_next_sessions:
                return
            elapsed = now - state.last_run
            if elapsed >= schedule.every_seconds and _within_active_hours(schedule, now):
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
                        "targets": [],
                        "connected_targets": [],
                        "indicator_only": False,
                    },
                    "suppressed_reason": f"{lock_reason}_skip_no_backfill",
                }
                try:
                    with open(state_path, "w") as f:
                        json.dump(state.to_dict(), f)
                except Exception:
                    pass
            return

        wake_requested = session.session_id in self.wake_sessions
        wake_reason = None
        if wake_requested:
            self.wake_sessions.discard(session.session_id)
            wake_reason = self.last_wake_reason.pop(session.session_id, None)
        wake_next = session.session_id in self.wake_next_sessions

        if not wake_requested:
            elapsed = now - state.last_run
            if elapsed < schedule.every_seconds:
                if wake_next:
                    return
                return
            if wake_next:
                self.wake_next_sessions.discard(session.session_id)
                wake_reason = self.last_wake_reason.pop(session.session_id, wake_reason)

        if not _within_active_hours(schedule, now):
            return

        # If delivery is explicit and no targets are currently connected,
        # skip the heartbeat run to avoid burning cycles before a client attaches.
        if not wake_requested and not wake_next and delivery.mode == "explicit":
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
                    with open(state_path, "w") as f:
                        json.dump(state.to_dict(), f)
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
            if _is_effectively_empty(heartbeat_content):
                state.last_run = now
                with open(state_path, "w") as f:
                    json.dump(state.to_dict(), f)
                return
        elif schedule.require_file:
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
    ):
        """Execute the heartbeat using the gateway engine."""
        self.busy_sessions.add(session.session_id)
        keep_busy_until_collect_finishes = False
        timed_out = False
        
        # Resolve wake_reason for tracing
        _wake_reason = self.last_wake_reason.get(session.session_id, "scheduled")
        
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
            
            # Use exec prompt for async completions, standard heartbeat otherwise
            if has_exec_completion:
                prompt = EXEC_EVENT_PROMPT
                logger.info("Using EXEC_EVENT_PROMPT for session %s (exec completion detected)", session.session_id)
            else:
                prompt = schedule.prompt.strip() or DEFAULT_HEARTBEAT_PROMPT
                if "{ok_token}" in prompt:
                    ok_token = schedule.ok_tokens[0] if schedule.ok_tokens else DEFAULT_OK_TOKENS[0]
                    prompt = prompt.replace("{ok_token}", ok_token)
            
            # Build metadata with system events
            heartbeat_investigation_only = str(
                os.getenv("UA_HEARTBEAT_INVESTIGATION_ONLY", "1")
            ).strip().lower() not in {"0", "false", "no", "off"}
            metadata: dict = {
                "source": "heartbeat",
                "heartbeat_investigation_only": heartbeat_investigation_only,
            }
            if system_events:
                metadata["system_events"] = system_events
                logger.info("Injecting %d system events into heartbeat for %s", len(system_events), session.session_id)

            todoist_actionable_count: Optional[int] = None
            todoist_brainstorm_candidate_count: Optional[int] = None

            # Deterministic Todoist pre-step: inject actionable summary and/or
            # brainstorm candidates when present.
            try:
                from universal_agent.services.todoist_service import TodoService

                todoist = TodoService()
                summary = todoist.heartbeat_summary()
                actionable = int(summary.get("actionable_count") or 0)
                todoist_actionable_count = actionable
                candidates = []
                try:
                    candidates = todoist.heartbeat_brainstorm_candidates(limit=3)
                except Exception:
                    candidates = []
                todoist_brainstorm_candidate_count = len(candidates)

                if candidates:
                    brainstorm_event = {
                        "type": "todoist_brainstorm_candidates",
                        "payload": {
                            "count": len(candidates),
                            "candidates": candidates,
                        },
                        "created_at": datetime.now().isoformat(),
                        "session_id": session.session_id,
                    }
                    system_events.append(brainstorm_event)
                    metadata["system_events"] = system_events
                    metadata["todoist_brainstorm_candidates"] = candidates

                if actionable > 0:
                    todoist_event = {
                        "type": "todoist_summary",
                        "payload": summary,
                        "created_at": datetime.now().isoformat(),
                        "session_id": session.session_id,
                    }
                    system_events.append(todoist_event)
                    metadata["system_events"] = system_events
                    metadata["todoist_summary"] = summary
                    logger.info(
                        "Injected Todoist heartbeat summary (%d actionable) into heartbeat for %s",
                        actionable,
                        session.session_id,
                    )
                elif candidates:
                    logger.info(
                        "Injected Todoist brainstorm candidates (%d) into heartbeat for %s",
                        len(candidates),
                        session.session_id,
                    )
            except Exception as exc:
                logger.info("Todoist heartbeat pre-step unavailable for %s: %s", session.session_id, exc)
            
            full_response = ""
            streamed_chunks: list[str] = []
            final_text: Optional[str] = None
            saw_streaming_text = False

            # If Todoist is available and there is no actionable work and no other system events,
            # skip running an expensive LLM heartbeat turn.
            should_skip_agent_run = (
                todoist_actionable_count is not None
                and todoist_actionable_count <= 0
                and (todoist_brainstorm_candidate_count or 0) <= 0
                and not system_events
                and not has_exec_completion
            )

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
                    "Skipping heartbeat agent execution for %s (no actionable Todoist tasks)",
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
            
            with open(state_path, "w") as f:
                json.dump(state.to_dict(), f)
            self._emit_event(
                {
                    "type": "heartbeat_completed",
                    "session_id": session.session_id,
                    "timestamp": datetime.now().isoformat(),
                    "ok_only": ok_only,
                    "suppressed_reason": suppressed_reason,
                    "sent": sent_any,
                }
            )

        except Exception as e:
            logger.error(f"Heartbeat execution failed for {session.session_id}: {e}")
            self._emit_event(
                {
                    "type": "heartbeat_failed",
                    "session_id": session.session_id,
                    "timestamp": datetime.now().isoformat(),
                    "error": str(e),
                }
            )
            # Update last_run even on failure to prevent rapid retry loops.
            # The next scheduled tick will retry after the normal interval.
            state.last_run = time.time()
            try:
                with open(state_path, "w") as f:
                    json.dump(state.to_dict(), f)
            except Exception:
                pass
        finally:
            # Close the heartbeat Logfire span
            if _hb_span is not None:
                try:
                    _hb_span.__exit__(None, None, None)
                except Exception:
                    pass
            if not keep_busy_until_collect_finishes:
                self.busy_sessions.discard(session.session_id)
