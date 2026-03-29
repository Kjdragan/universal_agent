import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Optional, Dict

from universal_agent.gateway import GatewaySession, GatewayRequest

logger = logging.getLogger(__name__)

TODO_DISPATCH_PROMPT = """
You are the Pipeline Orchestrator. Your exclusive job is to execute the assigned tasks from the Task Queue below to completion.
Do not perform any system monitoring, infrastructure checks, or background reporting.
Your only goal is to execute the assigned tasks, deliver results, then disposition them.

### Tool Constraints (CRITICAL):
- To interact with Task Hub (the To-Do list framework where tasks are tracked), strictly use `mcp__internal__task_hub_task_action`.
- To send emails, strictly use `mcp__internal__send_agentmail`.
- NEVER write Python scripts, Bash scripts, or use `curl` to interact with AgentMail or Task Hub. Exclusively use the provided native MCP tools.
- `todoist` is deprecated and permanently removed. ALL missions are managed through Task Hub.

### VP Delegation Fallback (CRITICAL):
If you attempt to delegate a mission to a VP Gateway (e.g., `vp.general.primary` or `vp.coder.primary`) and the connection is refused, DO NOT give up, block the task, or attempt to absorb the work yourself. 
Instead, you must fix the root cause:
1. Use your Bash tool to wake up the offline VP worker. 
   - Try: `bash scripts/start_vp_worker.sh vp.general.primary &` (in the background) or `systemctl --user start ua-vp-general.service` 
2. Wait 5-10 seconds for the worker to initialize.
3. Validate it's listening, then retry the delegation to the VP Gateway.

After finishing work, ALWAYS disposition every claimed task via `mcp__internal__task_hub_task_action` (`complete`, `review`, `block`, or `park`).
"""

class ToDoDispatchService:
    def __init__(self, connection_manager=None, event_callback=None):
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.active_sessions: Dict[str, GatewaySession] = {}
        self.wake_sessions = set()
        self.connection_manager = connection_manager
        self.event_callback = event_callback

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._scheduler_loop())
        logger.info("📋 ToDo Dispatch Service started")

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
        logger.info("📋 ToDo Dispatch Service stopped")
        
    def register_session(self, session: GatewaySession):
        logger.info(f"Registering session {session.session_id} for todo_dispatch")
        self.active_sessions[session.session_id] = session

    def unregister_session(self, session_id: str):
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]

    def request_dispatch_now(self, session_id: str) -> None:
        self.wake_sessions.add(session_id)
        if self.event_callback:
            self.event_callback({
                "type": "todo_dispatch_wake_requested",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat()
            })
        logger.info("ToDo dispatch requested for %s", session_id)

    async def _scheduler_loop(self):
        while self.running:
            try:
                start_time = time.time()
                for session_id, session in list(self.active_sessions.items()):
                    if session.session_id in self.wake_sessions:
                        self.wake_sessions.remove(session.session_id)
                        await self._process_session(session)
                
                elapsed = time.time() - start_time
                sleep_time = max(0.5, 2.0 - elapsed)
                await asyncio.sleep(sleep_time)
            except Exception as e:
                logger.critical(f"ToDo Dispatch scheduler crash: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _process_session(self, session: GatewaySession):
        from universal_agent.durable.db import connect_runtime_db
        from universal_agent.services.dispatch_service import dispatch_sweep
        import traceback

        try:
            with connect_runtime_db() as conn:
                task_hub_claimed = dispatch_sweep(
                    conn,
                    agent_id="simone",
                    limit=5,
                    workspace_dir=session.workspace_dir
                )
            
            if not task_hub_claimed:
                logger.debug("No tasks claimed for todo_dispatch.")
                return

            task_ids = sorted({str(item.get("task_id") or "").strip() for item in task_hub_claimed})
            logger.info("Dispatching %d tasks to %s: %s", len(task_hub_claimed), session.session_id, task_ids)

            # Build Prompt
            lines = ["== TASK QUEUE =="]
            lines.append(f"You have {len(task_hub_claimed)} task(s) to process.")
            for idx, item in enumerate(task_hub_claimed, 1):
                t_id = str(item.get("task_id") or "")
                title = str(item.get("title") or "(untitled)")
                desc = str(item.get("description") or "").strip()
                lines.append(f"Task {idx}: [{t_id}] {title}")
                lines.append(f"Description: {desc}")
            
            prompt = f"{TODO_DISPATCH_PROMPT}\n\n" + "\n".join(lines)

            # Provide visibility of progress specifically for To-Do List Tab UI
            if self.event_callback:
                self.event_callback({
                    "type": "agent_state_changed",
                    "session_id": session.session_id,
                    "event": {
                        "state": "processing",
                        "source": "todo_dispatcher",
                        "timestamp": datetime.now().isoformat()
                    }
                })

            req = GatewayRequest(
                message=prompt,
                source="todo_dispatcher",
                tags=["todo_dispatch"]
            )
            
            if self.connection_manager:
                await self.connection_manager.dispatch_request(session.session_id, req)
                
        except Exception as e:
            logger.error("Failed to process todo_dispatch for %s: %s", session.session_id, e)
            logger.error(traceback.format_exc())
            
        finally:
            if self.event_callback:
                self.event_callback({
                    "type": "agent_state_changed",
                    "session_id": session.session_id,
                    "event": {
                        "state": "idle",
                        "source": "todo_dispatcher",
                        "timestamp": datetime.now().isoformat()
                    }
                })
