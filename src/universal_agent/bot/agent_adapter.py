import os
import sys
import asyncio
from typing import Optional, Any
from dataclasses import dataclass
from .execution_logger import ExecutionLogger

@dataclass
class AgentRequest:
    prompt: str
    workspace_dir: str
    reply_future: asyncio.Future

class AgentAdapter:
    def __init__(self):
        self.initialized = False
        self.options = None
        self.session = None
        self.user_id = None
        self.workspace_dir = None
        self.trace = None
        
        # Async Actor state
        self.request_queue = asyncio.Queue()
        self.worker_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    async def initialize(self):
        """Initialize a fresh agent session and start background worker."""
        if self.initialized:
            return

        from universal_agent.main import setup_session
        
        print("🤖 Initializing Agent Session...")
        self.options, self.session, self.user_id, self.workspace_dir, self.trace = await setup_session()
        
        # Start background worker loop
        self._shutdown_event.clear()
        self.worker_task = asyncio.create_task(self._client_actor_loop())
        
        self.initialized = True
        print(f"🤖 Agent Session Initialized. Workspace: {self.workspace_dir}")

    async def _client_actor_loop(self):
        """Background task that keeps Client context alive."""
        from universal_agent.main import ClaudeSDKClient, process_turn
        
        print("🧵 Agent Client Actor Loop Started")
        try:
            # Proper context usage within a single task
            async with ClaudeSDKClient(self.options) as client:
                while not self._shutdown_event.is_set():
                    # Wait for next request
                    # We use a timeout to allow checking shutdown event occasionally if needed,
                    # though queue.get() is cancellable so it's fine.
                    try:
                        request: AgentRequest = await self.request_queue.get()
                    except asyncio.CancelledError:
                        break
                        
                    if request is None: # Sentinel for shutdown
                        self.request_queue.task_done()
                        break
                        
                    try:
                        print(f"🧵 Actor processing prompt: {request.prompt[:50]}...")
                        result = await process_turn(client, request.prompt, request.workspace_dir)
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
            # If the loop crashes, we can't serve requests anymore.
            # We might want to set initialized=False?
            import traceback
            traceback.print_exc()
        finally:
             print("🧵 Agent Client Actor Loop Exiting")

    async def reinitialize(self):
        """Force a fresh session."""
        print("🔄 Reinitializing Agent Session (fresh start)...")
        await self.shutdown()
        
        # Reset state
        self.initialized = False
        self.options = None
        self.session = None
        self.user_id = None
        self.workspace_dir = None
        self.trace = None
        
        # Fresh init
        await self.initialize()

    async def execute(self, task, continue_session: bool = False):
        """
        Executes a task using the agent via the background actor.
        """
        # Session management
        if not continue_session or not self.initialized:
            await self.reinitialize()
        else:
            print("🔗 Continuing in existing session...")

        # Setup Logging
        log_file_name = f"task_{task.id}.log"
        log_file_path = os.path.join(self.workspace_dir, log_file_name)
        task.log_file = log_file_path
        task.workspace = self.workspace_dir
        
        # Setup logging context wraps the WAIT for result, not the execution itself
        # This means logging inside process_turn matches the ACTOR's stdout/stderr context?
        # WARNING: ExecutionLogger redirects sys.stdout. If we redirect in THIS task (main thread),
        # but the ACTOR is running in background... stdout capture works globally for the process mostly?
        # Actually Python's sys.stdout is global. So if we redirect here, and await the future...
        # The ACTOR printing in background WILL be captured by THIS redirection because it happens in same process/time.
        # Provided we await the result while the context is active.
        
        with ExecutionLogger(log_file_path):
            print(f"=== Task Execution Start: {task.id} ===")
            print(f"User ID: {task.user_id}")
            print(f"Prompt: {task.prompt}")
            print("-" * 50)
            
            # Create Future for result
            reply_future = asyncio.get_running_loop().create_future()
            req = AgentRequest(
                prompt=task.prompt,
                workspace_dir=self.workspace_dir,
                reply_future=reply_future
            )
            
            try:
                # Send to actor
                await self.request_queue.put(req)
                
                # Wait for result
                result = await reply_future
                
                # Store data
                task.execution_summary = result
                task.result = result.response_text
                
                if not task.result:
                    task.result = "(No text response returned by agent)"

            except Exception as e:
                print(f"🔥 Critical Error during execution: {e}")
                task.status = "error"
                task.result = f"Error: {e}"
                # If actor crashed, trigger reinit logic on next run?
                if self.worker_task and self.worker_task.done():
                    self.initialized = False
            
            print("-" * 50)
            print(f"=== Task Execution End: {task.id} ===")
            
    async def shutdown(self):
        """Cleanup resources."""
        self._shutdown_event.set()
        if self.worker_task and not self.worker_task.done():
            # Send sentinel
            await self.request_queue.put(None)
            try:
                # Wait for graceful exit
                await asyncio.wait_for(self.worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.worker_task.cancel()
                try:
                    await self.worker_task
                except asyncio.CancelledError:
                    pass
        self.worker_task = None
        self.request_queue = asyncio.Queue() # Clear queue
