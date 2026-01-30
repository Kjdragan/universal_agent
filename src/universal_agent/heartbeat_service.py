
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.gateway import InProcessGateway, GatewaySession, GatewayRequest

logger = logging.getLogger(__name__)

import hashlib
import re

# Constants
HEARTBEAT_FILE = "HEARTBEAT.md"
HEARTBEAT_STATE_FILE = "heartbeat_state.json"
DEFAULT_INTERVAL = int(os.getenv("UA_HEARTBEAT_INTERVAL", "300"))  # 5 minutes default
BUSY_RETRY_DELAY = 10  # Seconds
HEARTBEAT_EXECUTION_TIMEOUT = int(os.getenv("UA_HEARTBEAT_EXEC_TIMEOUT", "45"))

@dataclass
class HeartbeatDeliveryConfig:
    mode: str = "last"  # last | explicit | none
    # Future: channel, to, etc.

@dataclass
class HeartbeatVisibilityConfig:
    show_ok: bool = False
    show_alerts: bool = True
    dedupe_window_seconds: int = 86400  # 24 hours

@dataclass
class HeartbeatState:
    last_run: float = 0.0
    last_message_hash: Optional[str] = None
    last_message_ts: float = 0.0
    
    def to_dict(self):
        return {
            "last_run": self.last_run,
            "last_message_hash": self.last_message_hash,
            "last_message_ts": self.last_message_ts,
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            last_run=data.get("last_run", 0.0),
            last_message_hash=data.get("last_message_hash"),
            last_message_ts=data.get("last_message_ts", 0.0),
        )

class HeartbeatService:
    def __init__(self, gateway: InProcessGateway, connection_manager):
        self.gateway = gateway
        self.connection_manager = connection_manager
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.active_sessions: Dict[str, GatewaySession] = {}
        # Simple tracking of busy sessions (primitive lock)
        self.busy_sessions: set[str] = set()
        
        # MOCK CONFIG (In future, load from session config)
        self.default_delivery = HeartbeatDeliveryConfig(
            mode=os.getenv("UA_HB_DELIVERY_MODE", "last")
        )
        self.default_visibility = HeartbeatVisibilityConfig(
            show_ok=os.getenv("UA_HB_SHOW_OK", "false").lower() == "true",
            show_alerts=os.getenv("UA_HB_SHOW_ALERTS", "true").lower() == "true",
            dedupe_window_seconds=int(os.getenv("UA_HB_DEDUPE_WINDOW", "86400"))
        )

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

    async def _scheduler_loop(self):
        """Main loop that checks sessions periodically."""
        logger.info("Heartbeat scheduler loop starting")
        while self.running:
            try:
                # We use a simple 10s tick for the MVP; production would use a heap
                start_time = time.time()
                
                count = len(self.active_sessions)
                if count > 0:
                    logger.info(f"Heartbeat tick: {count} active sessions")
                    # import sys; sys.stderr.write(f"DEBUG: TICK {count}\n") # Removed noisy debug
                
                # Use list snapshot to avoid runtime errors
                for session_id, session in list(self.active_sessions.items()):
                    try:
                        await self._process_session(session)
                    except Exception as e:
                        logger.error(f"Error processing heartbeat for {session_id}: {e}")
                
                # Sleep remainder of tick
                elapsed = time.time() - start_time
                sleep_time = max(1.0, 5.0 - elapsed) # Reduced to 5s for test
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

        # Check schedule (MVP: fixed 5m interval)
        now = time.time()
        elapsed = now - state.last_run
        if elapsed < DEFAULT_INTERVAL:
            return

        # Check HEARTBEAT.md
        hb_file = workspace / HEARTBEAT_FILE
        if not hb_file.exists():
            return
            
        content = hb_file.read_text().strip()
        if not content:
            return

        # Ready to run
        logger.info(f"💓 Triggering heartbeat for {session.session_id}")
        await self._run_heartbeat(session, state, state_path, content)

    async def _run_heartbeat(
        self,
        session: GatewaySession,
        state: HeartbeatState,
        state_path: Path,
        heartbeat_content: str,
    ):
        """Execute the heartbeat using the gateway engine."""
        self.busy_sessions.add(session.session_id)
        
        # Use simple mock configs (MVP)
        delivery = self.default_delivery
        visibility = self.default_visibility
        
        def _mock_heartbeat_response(content: str) -> str:
            # Deterministic response for tests/CI (no external calls).
            for token in ("UA_HEARTBEAT_OK", "ALERT_TEST_A", "ALERT_TEST_B"):
                if token in content:
                    return token
            match = re.search(r"'([^']+)'", content)
            if match:
                return match.group(1)
            return "UA_HEARTBEAT_OK"

        try:
            # Construct prompt based on HEARTBEAT.md (already verified existing/non-empty)
            prompt = (
                "SYSTEM HEARTBEAT EVENT:\n"
                f"Please read {HEARTBEAT_FILE} to check for any pending instructions or context updates. "
                "If there is nothing new to do, reply with 'UA_HEARTBEAT_OK'."
            )
            
            request = GatewayRequest(
                user_input=prompt,
                force_complex=False,
                metadata={"source": "heartbeat"}
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
            
            ok_only = "UA_HEARTBEAT_OK" in full_response
            is_duplicate = False
            msg_hash = hashlib.sha256(full_response.encode()).hexdigest()
            now = time.time()
            
            # Policy 1: Visibility (showOk)
            suppress_ok = ok_only and not visibility.show_ok
            
            # Policy 2: Deduplication
            if not ok_only: # Only dedupe alerts, not OKs (OKs handled by showOk)
                if state.last_message_hash == msg_hash:
                    # Check window
                    if (now - state.last_message_ts) < visibility.dedupe_window_seconds:
                        is_duplicate = True
                        logger.info(f"Suppressed duplicate alert for {session.session_id} (hash={msg_hash[:8]})")
            
            # Policy 3: Delivery Mode
            should_send = True
            if delivery.mode == "none":
                should_send = False
            elif suppress_ok:
                should_send = False
                logger.info(f"Suppressed OK heartbeat for {session.session_id} (show_ok=False)")
            elif is_duplicate:
                should_send = False
            
            if should_send:
                summary_event = {
                    "type": "heartbeat_summary",
                    "data": {
                        "text": full_response,
                        "timestamp": datetime.now().isoformat(),
                        "ok_only": ok_only,
                        # Add extra metadata for UI awareness
                        "delivered": {
                            "mode": delivery.mode,
                            "is_duplicate": is_duplicate, # Should be false if sent
                        }
                    },
                }
                await self.connection_manager.broadcast(session.session_id, summary_event)
                
                # Update last message state only if sent (so we don't dedupe against something we never showed)
                # Actually, for dedupe, if we suppressed A because it was A, we keep the OLD timestamp (so window doesn't reset).
                # But if we sent it, we update.
                if not ok_only:
                    state.last_message_hash = msg_hash
                    state.last_message_ts = now

            # Always update last_run to respect interval
            state.last_run = now
            
            with open(state_path, "w") as f:
                json.dump(state.to_dict(), f)

        except Exception as e:
            logger.error(f"Heartbeat execution failed for {session.session_id}: {e}")
        finally:
            self.busy_sessions.discard(session.session_id)
