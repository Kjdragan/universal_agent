import os
import sys
import asyncio
import uuid
import logging
from typing import Optional, Any, AsyncIterator
from dataclasses import dataclass
from .config import UA_GATEWAY_URL
from universal_agent.gateway import (
    Gateway, 
    InProcessGateway, 
    ExternalGateway, 
    GatewayRequest, 
    GatewaySession
)
from universal_agent.agent_core import AgentEvent, EventType, UniversalAgent

# Set up logging
logger = logging.getLogger(__name__)

@dataclass
class AgentRequest:
    prompt: str
    user_id: str
    workspace_dir: Optional[str]
    reply_future: asyncio.Future

class AgentAdapter:
    """
    Adapts the Telegram Bot to the Universal Agent Gateway.
    Acts as a client, whether the Gateway is in-process or external.
    """
    def __init__(self):
        self.gateway: Optional[Gateway] = None
        self.initialized = False
        
        # Async Actor state
        self.request_queue = asyncio.Queue()
        self.worker_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    async def initialize(self):
        """Initialize Connection to Gateway."""
        if self.initialized:
            return

        print("🤖 Initializing Agent Gateway Connection...")
        
        if UA_GATEWAY_URL:
            print(f"🌍 Connecting to External Gateway at {UA_GATEWAY_URL}...")
            self.gateway = ExternalGateway(base_url=UA_GATEWAY_URL)
            # Verify connection
            if not await self.gateway.health_check():
                print("⚠️  Warning: External Gateway health check failed.")
        else:
            print("🏠 Starting In-Process Gateway...")
            self.gateway = InProcessGateway()
            
        # Start background worker loop
        self._shutdown_event.clear()
        self.worker_task = asyncio.create_task(self._client_actor_loop())
        
        self.initialized = True
        print("✅ Agent Adapter Initialized")

    async def _get_or_create_session(self, user_id: str) -> GatewaySession:
        """
        Map Telegram User ID to a consistent Gateway Session.
        Format: tg_{user_id}
        """
        # We use a deterministic session ID so the user resumes the same "chat"
        # In the future, we could support multiple sessions per user via commands
        session_id = f"tg_{user_id}"
        
        try:
            # Try to resume first
            return await self.gateway.resume_session(session_id)
        except ValueError:
            # Create new if not found
            print(f"🆕 Creating new session for {user_id}...")
            
            # Fix Session Amnesia:
            # We must provide a workspace_dir ending in our desired session_id (tg_{user_id})
            # so that InProcessGateway uses that ID instead of generating a random one.
            workspace_dir = os.path.join("AGENT_RUN_WORKSPACES", session_id)
            
            return await self.gateway.create_session(user_id=f"telegram_{user_id}", workspace_dir=workspace_dir)

    async def _client_actor_loop(self):
        """Background task that processes requests sequentially."""
        print("🧵 Agent Client Actor Loop Started")
        try:
            while not self._shutdown_event.is_set():
                try:
                    request: AgentRequest = await self.request_queue.get()
                except asyncio.CancelledError:
                    break
                    
                if request is None: # Sentinel for shutdown
                    self.request_queue.task_done()
                    break
                    
                try:
                    print(f"🧵 Actor processing prompt from {request.user_id}: {request.prompt[:50]}...")
                    
                    if not self.gateway:
                        raise RuntimeError("Gateway not initialized")

                    # 1. Get Session
                    session = await self._get_or_create_session(request.user_id)
                    
                    # 2. Execute Request
                    gw_req = GatewayRequest(
                        user_input=request.prompt,
                        force_complex=False, # Could expose via command
                        metadata={"source": "telegram"}
                    )
                    
                    # 3. Collect Response (Accumulate streaming events for now, or unified result)
                    # The Gateway.run_query convenience method is perfect for non-streaming bot logic
                    # But we might want streaming later. For now, let's use run_query for simplicity
                    # to match the expected "reply_future.set_result(execution_summary)" interface
                    # required by the Task Manager for notification formatting.
                    
                    result = await self.gateway.run_query(session, gw_req)
                    
                    # 4. Return Result
                    # The TaskManager expects an object with .response_text and .tool_calls etc.
                    # GatewayResult matches this duck-typing mostly, but let's verify.
                    # GatewayResult(response_text, tool_calls, trace_id, metadata)
                    # ExecutionResult(response_text, execution_time...)
                    # We might need to map it or ensure format_telegram_response handles it.
                    
                    if not request.reply_future.done():
                        request.reply_future.set_result(result)
                        
                except Exception as e:
                    print(f"🔥 Actor Error: {e}")
                    if not request.reply_future.done():
                        request.reply_future.set_exception(e)
                finally:
                    self.request_queue.task_done()
                        
        except asyncio.CancelledError:
            print("🧵 Agent Client Actor Loop Cancelled")
        except Exception as e:
            print(f"🔥 Agent Client Actor Loop Crashing: {e}")
            import traceback
            traceback.print_exc()
        finally:
             print("🧵 Agent Client Actor Loop Exiting")
             # Clean up external gateway resources if needed
             if isinstance(self.gateway, ExternalGateway):
                 await self.gateway.close()

    async def shutdown(self):
        """Cleanup resources."""
        self._shutdown_event.set()
        if self.worker_task and not self.worker_task.done():
            await self.request_queue.put(None)
            try:
                await asyncio.wait_for(self.worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.worker_task.cancel()
                try:
                    await self.worker_task
                except asyncio.CancelledError:
                    pass
        self.worker_task = None
        self.initialized = False

    async def execute(self, task, continue_session: bool = False):
        """
        Executes a task using the agent via the background actor.
        Matches the interface expected by TaskManager.
        """
        if not self.initialized:
            await self.initialize()

        # Create Future for result
        reply_future = asyncio.get_running_loop().create_future()
        req = AgentRequest(
            prompt=task.prompt,
            user_id=str(task.user_id),
            workspace_dir=None, # Managed by Session mappings now
            reply_future=reply_future
        )
        
        try:
            # Send to actor
            await self.request_queue.put(req)
            
            # Wait for result with TIMEOUT
            # 5 minutes max for any single turn
            result = await asyncio.wait_for(reply_future, timeout=300.0)
            
            # Store data
            task.execution_summary = result
            task.result = result.response_text
            
            # Helper for logging (Gateway Result might not have path)
            # We could fetch session info to get path if needed for logging artifact sending
            
            if not task.result:
                task.result = "(No text response returned by agent)"

        except Exception as e:
            print(f"🔥 Critical Error during execution: {e}")
            task.status = "error"
            task.result = f"Error: {e}"
