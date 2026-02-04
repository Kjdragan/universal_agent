
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.gateway import InProcessGateway, GatewaySession, GatewayRequest

logger = logging.getLogger(__name__)

import hashlib
import re

import pytz

# Constants
HEARTBEAT_FILE = "HEARTBEAT.md"
HEARTBEAT_STATE_FILE = "heartbeat_state.json"
DEFAULT_HEARTBEAT_PROMPT = (
    "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. "
    "Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK."
)
DEFAULT_INTERVAL_SECONDS = 30 * 60  # 30 minutes default (Clawdbot parity)
BUSY_RETRY_DELAY = 10  # Seconds
HEARTBEAT_EXECUTION_TIMEOUT = int(os.getenv("UA_HEARTBEAT_EXEC_TIMEOUT", "45"))
DEFAULT_ACK_MAX_CHARS = 300
DEFAULT_OK_TOKENS = ["HEARTBEAT_OK", "UA_HEARTBEAT_OK"]

# Specialized prompt for exec completion events (Clawdbot parity)
EXEC_EVENT_PROMPT = (
    "An async command you ran earlier has completed. The result is shown in the system messages above. "
    "Please relay the command output to the user in a helpful way. If the command succeeded, share the relevant output. "
    "If it failed, explain what went wrong."
)

# Type alias for system event provider callbacks
from typing import Callable
SystemEventProvider = Callable[[str], list[dict]]  # (session_id) -> list of event dicts

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
    for token in tokens_sorted:
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
    ):
        self.gateway = gateway
        self.connection_manager = connection_manager
        self.system_event_provider = system_event_provider
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.active_sessions: Dict[str, GatewaySession] = {}
        # Simple tracking of busy sessions (primitive lock)
        self.busy_sessions: set[str] = set()
        self.wake_sessions: set[str] = set()
        self.wake_next_sessions: set[str] = set()
        self.last_wake_reason: Dict[str, str] = {}
        
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
        logger.info("Heartbeat wake requested for %s (%s)", session_id, reason)

    def request_heartbeat_next(self, session_id: str, reason: str = "wake_next") -> None:
        self.wake_next_sessions.add(session_id)
        self.last_wake_reason[session_id] = reason
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

    async def _process_session(self, session: GatewaySession):
        """Check if a session needs a heartbeat run."""
        if session.session_id in self.busy_sessions:
            # logger.info(f"Session {session.session_id} is busy.")
            return  # Skip if busy executing normal request

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
                return

        # Check HEARTBEAT.md (optional)
        hb_file = workspace / HEARTBEAT_FILE
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
            metadata: dict = {"source": "heartbeat"}
            if system_events:
                metadata["system_events"] = system_events
                logger.info("Injecting %d system events into heartbeat for %s", len(system_events), session.session_id)
            
            request = GatewayRequest(
                user_input=prompt,
                force_complex=False,
                metadata=metadata,
            )
            
            full_response = ""

            if os.getenv("UA_HEARTBEAT_MOCK_RESPONSE", "0").lower() in {"1", "true", "yes"}:
                full_response = _mock_heartbeat_response(heartbeat_content)
                logger.info("Heartbeat mock response enabled for %s", session.session_id)
            else:
                async def _collect_events() -> None:
                    nonlocal full_response
                    async for event in self.gateway.execute(session, request):
                        if event.type == EventType.TEXT:
                            if isinstance(event.data, dict):
                                full_response += event.data.get("text", "")
                            elif isinstance(event.data, str):
                                full_response += event.data

                try:
                    await asyncio.wait_for(_collect_events(), timeout=HEARTBEAT_EXECUTION_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.error(
                        "Heartbeat execution timed out after %ss for %s",
                        HEARTBEAT_EXECUTION_TIMEOUT,
                        session.session_id,
                    )
                    full_response = "UA_HEARTBEAT_TIMEOUT"

            logger.info(f"Heartbeat response for {session.session_id}: '{full_response}'")

            # --- Phase 3 Logic ---
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
                        "type": "heartbeat_indicator",
                        "data": {
                            "timestamp": datetime.now().isoformat(),
                            "ok_only": True,
                            "delivered": {
                                "mode": delivery.mode,
                                "targets": delivery_targets,
                            },
                        },
                    }
                else:
                    summary_event = {
                        "type": "heartbeat_summary",
                        "data": {
                            "text": summary_text,
                            "timestamp": datetime.now().isoformat(),
                            "ok_only": ok_only,
                            # Add extra metadata for UI awareness
                            "delivered": {
                                "mode": delivery.mode,
                                "targets": delivery_targets,
                                "is_duplicate": is_duplicate, # Should be false if sent
                            },
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
                "delivery": {
                    "mode": delivery.mode,
                    "targets": delivery_targets,
                    "connected_targets": connected_targets,
                    "indicator_only": allow_indicator,
                },
                "suppressed_reason": suppressed_reason,
            }

            # Always update last_run to respect interval
            state.last_run = now
            
            with open(state_path, "w") as f:
                json.dump(state.to_dict(), f)

        except Exception as e:
            logger.error(f"Heartbeat execution failed for {session.session_id}: {e}")
        finally:
            self.busy_sessions.discard(session.session_id)
