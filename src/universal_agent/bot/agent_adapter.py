import os
import sys
import asyncio
import uuid
import logging
from pathlib import Path
from typing import Optional, Any, AsyncIterator
from dataclasses import dataclass
from .config import UA_GATEWAY_URL, UA_TELEGRAM_ALLOW_INPROCESS
from universal_agent.gateway import (
    Gateway, 
    InProcessGateway, 
    ExternalGateway, 
    GatewayRequest, 
    GatewaySession
)
from universal_agent.agent_core import AgentEvent, EventType, UniversalAgent
from universal_agent.session_checkpoint import SessionCheckpointGenerator

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
            if not UA_TELEGRAM_ALLOW_INPROCESS:
                raise RuntimeError(
                    "UA_GATEWAY_URL is not set. "
                    "Set UA_GATEWAY_URL to use the external gateway, "
                    "or set UA_TELEGRAM_ALLOW_INPROCESS=1 for local dev."
                )
            print("🏠 Starting In-Process Gateway (local dev override enabled)...")
            self.gateway = InProcessGateway()
            
        # Start background worker loop
        self._shutdown_event.clear()
        self.worker_task = asyncio.create_task(self._client_actor_loop())
        
        self.initialized = True
        print("✅ Agent Adapter Initialized")

    async def _get_or_create_session(self, user_id: str, user_prompt: str = "") -> GatewaySession:
        """
        Create a fresh session for each query to prevent context overflow.
        Injects checkpoint from prior session to preserve continuity.
        
        Strategy: Fresh session per query + checkpoint injection
        - Prevents context token accumulation across multiple messages
        - Preserves continuity via structured checkpoint (~2-4k tokens)
        """
        session_id = f"tg_{user_id}"
        workspace_dir = os.path.join("AGENT_RUN_WORKSPACES", session_id)
        workspace_path = Path(workspace_dir)
        
        # Load prior checkpoint if exists
        prior_checkpoint_context = ""
        if workspace_path.exists():
            try:
                generator = SessionCheckpointGenerator(workspace_path)
                checkpoint = generator.load_latest()
                if checkpoint:
                    prior_checkpoint_context = checkpoint.to_markdown()
                    print(f"📋 Loaded prior checkpoint for {user_id} ({len(prior_checkpoint_context)} chars)")
            except Exception as e:
                print(f"⚠️ Failed to load checkpoint: {e}")
        
        # Always create fresh session (clears Claude SDK context)
        print(f"🆕 Creating fresh session for {user_id}...")
        session = await self.gateway.create_session(
            user_id=f"telegram_{user_id}", 
            workspace_dir=workspace_dir
        )
        
        # Inject prior checkpoint as context if available
        if prior_checkpoint_context:
            # Prefix the user's prompt with checkpoint context
            session._injected_context = prior_checkpoint_context
            print(f"📋 Will inject checkpoint context into first message")
        
        return session

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

                    # 1. Get Session (fresh per query, with checkpoint injection)
                    session = await self._get_or_create_session(request.user_id, request.prompt)
                    
                    # 2. Build Request (inject checkpoint context if available)
                    user_input = request.prompt
                    if hasattr(session, '_injected_context') and session._injected_context:
                        # Prepend checkpoint context to user's prompt
                        user_input = (
                            f"<prior_session_context>\n"
                            f"{session._injected_context}\n"
                            f"</prior_session_context>\n\n"
                            f"**New Request:**\n{request.prompt}"
                        )
                        print(f"📋 Injected checkpoint context into prompt")
                    
                    gw_req = GatewayRequest(
                        user_input=user_input,
                        force_complex=False,
                        metadata={"source": "telegram", "original_prompt": request.prompt}
                    )
                    
                    # 3. Execute
                    result = await self.gateway.run_query(session, gw_req)
                    
                    # 4. Generate Checkpoint for next session
                    try:
                        workspace_path = Path(session.workspace_dir)
                        generator = SessionCheckpointGenerator(workspace_path)
                        checkpoint = generator.generate_from_result(
                            session_id=session.session_id,
                            original_request=request.prompt,
                            result=result,
                        )
                        generator.save(checkpoint)
                        print(f"✅ Saved session checkpoint: {workspace_path / 'session_checkpoint.json'}")
                    except Exception as ckpt_err:
                        print(f"⚠️ Failed to save checkpoint: {ckpt_err}")
                    
                    # 5. Return Result
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
            # Default 15 minutes; override with UA_TELEGRAM_TASK_TIMEOUT_SECONDS
            timeout_s = float(os.getenv("UA_TELEGRAM_TASK_TIMEOUT_SECONDS", "900"))
            result = await asyncio.wait_for(reply_future, timeout=timeout_s)
            
            # Store data
            task.execution_summary = result
            task.result = result.response_text
            
            # Helper for logging (Gateway Result might not have path)
            # We could fetch session info to get path if needed for logging artifact sending
            
            if not task.result:
                task.result = "(No text response returned by agent)"

        except asyncio.TimeoutError:
            print("🔥 Critical Error during execution: timeout waiting for gateway result")
            task.status = "error"
            task.result = (
                "Error: request timed out waiting for the agent response. "
                "Try a shorter prompt or increase UA_TELEGRAM_TASK_TIMEOUT_SECONDS."
            )
        except Exception as e:
            msg = str(e)
            if not msg:
                msg = repr(e)
            print(f"🔥 Critical Error during execution: {msg}")
            task.status = "error"
            task.result = f"Error: {msg}"
