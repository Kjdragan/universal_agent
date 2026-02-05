
import logging
from typing import Callable, Awaitable, Any

logger = logging.getLogger(__name__)

class BotConnectionAdapter:
    """
    Adapts the Telegram Bot message sending mechanism to the
    HeartbeatService's expected connection manager interface.
    
    HeartbeatService expects:
    - connection_manager.session_connections: dict/set of active session IDs
    - connection_manager.broadcast(session_id, data): async method to send events
    """
    def __init__(self, send_message_callback: Callable[[str, str], Awaitable[Any]]):
        """
        Args:
            send_message_callback: Async function (user_id, text) -> None
        """
        self.send_message_callback = send_message_callback
        # Mock session_connections to satisfy HeartbeatService checks.
        # It needs to support `if target in self.connection_manager.session_connections`.
        self.session_connections = {}

    async def broadcast(self, session_id: str, data: dict):
        """
        Mimics ConnectionManager.broadcast but sends a Telegram message.
        """
        if data.get("type") == "heartbeat_summary":
            payload = data.get("data", {})
            text = payload.get("text")
            ok_only = payload.get("ok_only", False)
            
            # If it's just an OK message and not suppressed (delivered), we send it.
            # But usually HeartbeatService suppresses OKs unless visibility settings say otherwise.
            # If we are here, HeartbeatService decided we SHOULD send it.
            
            if not text:
                return
            
            # User ID extraction depends on how session_id map to telegram users.
            # In AgentAdapter, session_id = f"tg_{user_id}"
            if session_id.startswith("tg_"):
                user_id = session_id[3:]
                try:
                    await self.send_message_callback(user_id, text)
                    logger.info(f"💓 Sent proactive heartbeat to {user_id}")
                except Exception as e:
                    logger.error(f"Failed to send heartbeat to {user_id}: {e}")
        
        elif data.get("type") == "heartbeat_indicator":
             # Optional: Could send a subtle emoji or action status if supported
             pass

    def register_active_session(self, session_id: str):
        """Mark a session as 'connected' so HeartbeatService thinks it can send."""
        self.session_connections[session_id] = {"bot_virtual_connection"}

    def unregister_active_session(self, session_id: str):
        if session_id in self.session_connections:
            del self.session_connections[session_id]
