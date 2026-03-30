import asyncio
import logging
import time
from datetime import datetime
from typing import Optional, Dict, Any, Callable, Awaitable

from universal_agent.gateway import GatewaySession, GatewayRequest

logger = logging.getLogger(__name__)

TODO_DISPATCH_PROMPT = """
You are Simone, the Pipeline Orchestrator. Your exclusive job is to execute the assigned tasks from the Task Queue below to completion.
Do not perform any system monitoring, infrastructure checks, or background reporting.
Your first step is always batch triage: decide which tasks you will handle personally versus which should be delegated to Cody or Atlas.
Use the current capacity snapshot and active assignment list below when making delegation decisions.
Your only goal is to execute the assigned tasks, deliver results, then disposition them.

### Tool Constraints (CRITICAL):
- To interact with Task Hub (the To-Do list framework where tasks are tracked), strictly use `mcp__internal__task_hub_task_action`.
- To send emails, strictly use `mcp__internal__send_agentmail`.
- NEVER write Python scripts, Bash scripts, or use `curl` to interact with AgentMail or Task Hub. Exclusively use the provided native MCP tools.
- Legacy external task-manager flows are retired. ALL missions are managed through Task Hub.

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
    def __init__(
        self,
        execution_callback: Optional[Callable[[str, GatewayRequest], Awaitable[dict[str, Any]]]] = None,
        event_callback=None,
    ):
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.active_sessions: Dict[str, GatewaySession] = {}
        self.wake_sessions = set()
        self.execution_callback = execution_callback
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
        if self.event_callback:
            self.event_callback({
                "type": "todo_dispatch_session_registered",
                "session_id": session.session_id,
                "timestamp": datetime.now().isoformat(),
                "wake_pending": session.session_id in self.wake_sessions,
            })

    def unregister_session(self, session_id: str):
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
        if self.event_callback:
            self.event_callback({
                "type": "todo_dispatch_session_unregistered",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
            })

    def request_dispatch_now(self, session_id: str) -> None:
        self.wake_sessions.add(session_id)
        if self.event_callback:
            self.event_callback({
                "type": "todo_dispatch_wake_requested",
                "session_id": session_id,
                "timestamp": datetime.now().isoformat(),
                "registered": session_id in self.active_sessions,
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
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
        from universal_agent.services.dispatch_service import dispatch_sweep
        from universal_agent.services.capacity_governor import CapacityGovernor, capacity_snapshot
        from universal_agent import task_hub
        import traceback

        claimed_assignment_ids: list[str] = []
        activity_db_path = get_activity_db_path()
        try:
            governor = CapacityGovernor.get_instance()
            capacity_ok, capacity_reason = governor.can_dispatch()
            if not capacity_ok:
                self.wake_sessions.add(session.session_id)
                if self.event_callback:
                    self.event_callback({
                        "type": "todo_dispatch_deferred",
                        "session_id": session.session_id,
                        "timestamp": datetime.now().isoformat(),
                        "reason": capacity_reason,
                    })
                logger.info(
                    "ToDo dispatch deferred for %s: %s",
                    session.session_id,
                    capacity_reason,
                )
                return

            # Task Hub state lives in the dedicated activity DB, not runtime_state.db.
            with connect_runtime_db(activity_db_path) as conn:
                task_hub_claimed = dispatch_sweep(
                    conn,
                    agent_id=f"todo:{session.session_id}",
                    limit=5,
                    provider_session_id=session.session_id,
                    workspace_dir=session.workspace_dir,
                )
            
            if not task_hub_claimed:
                if self.event_callback:
                    self.event_callback({
                        "type": "todo_dispatch_no_tasks",
                        "session_id": session.session_id,
                        "timestamp": datetime.now().isoformat(),
                    })
                logger.debug("No tasks claimed for todo_dispatch.")
                return

            claimed_assignment_ids = [
                str(item.get("assignment_id") or "").strip()
                for item in task_hub_claimed
                if str(item.get("assignment_id") or "").strip()
            ]
            task_ids = sorted({str(item.get("task_id") or "").strip() for item in task_hub_claimed})
            logger.info("Dispatching %d tasks to %s: %s", len(task_hub_claimed), session.session_id, task_ids)
            if self.event_callback:
                self.event_callback({
                    "type": "todo_dispatch_claimed",
                    "session_id": session.session_id,
                    "timestamp": datetime.now().isoformat(),
                    "task_ids": task_ids,
                    "assignment_ids": claimed_assignment_ids,
                    "task_count": len(task_ids),
                })

            # Build Prompt
            lines = ["== TASK QUEUE =="]
            lines.append(f"You have {len(task_hub_claimed)} task(s) to process.")
            snapshot = capacity_snapshot()
            lines.append("")
            lines.append("== CAPACITY SNAPSHOT ==")
            lines.append(
                "available_slots={available_slots} active_slots={active_slots} max_concurrent={max_concurrent} in_backoff={in_backoff}".format(
                    available_slots=snapshot.get("available_slots"),
                    active_slots=snapshot.get("active_slots"),
                    max_concurrent=snapshot.get("max_concurrent"),
                    in_backoff=snapshot.get("in_backoff"),
                )
            )
            lines.append("")
            lines.append("== ACTIVE ASSIGNMENTS ==")
            with connect_runtime_db(activity_db_path) as conn:
                activity = task_hub.get_agent_activity(conn)
            active_assignments = activity.get("active_assignments") if isinstance(activity, dict) else []
            if isinstance(active_assignments, list) and active_assignments:
                for assignment in active_assignments[:10]:
                    lines.append(
                        "- {agent} · {task_id} · {title}".format(
                            agent=str(assignment.get("agent_id") or "unknown"),
                            task_id=str(assignment.get("task_id") or ""),
                            title=str(assignment.get("title") or "").strip() or "(untitled)",
                        )
                    )
            else:
                lines.append("- none")
            lines.append("")
            for idx, item in enumerate(task_hub_claimed, 1):
                t_id = str(item.get("task_id") or "")
                title = str(item.get("title") or "(untitled)")
                desc = str(item.get("description") or "").strip()
                lines.append(f"Task {idx}: [{t_id}] {title}")
                lines.append(f"Description: {desc}")
                routing = item.get("_routing") if isinstance(item.get("_routing"), dict) else {}
                if routing:
                    lines.append(f"Routing Hint: {routing}")
            
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
                user_input=prompt,
                force_complex=True,
                metadata={
                    "source": "todo_dispatcher",
                    "dispatch_kind": "todo",
                    "claimed_task_ids": task_ids,
                    "claimed_assignment_ids": claimed_assignment_ids,
                },
            )
            
            if self.execution_callback:
                dispatch_result = await self.execution_callback(session.session_id, req)
                decision = str((dispatch_result or {}).get("decision") or "accepted").strip().lower()
                if self.event_callback:
                    self.event_callback({
                        "type": "todo_dispatch_submitted",
                        "session_id": session.session_id,
                        "timestamp": datetime.now().isoformat(),
                        "decision": decision,
                        "task_ids": task_ids,
                        "assignment_ids": claimed_assignment_ids,
                    })
                if decision != "accepted":
                    raise RuntimeError(f"todo_dispatch_not_admitted:{decision}")
            else:
                raise RuntimeError("todo dispatch execution callback is not configured")
                
        except Exception as e:
            logger.error("Failed to process todo_dispatch for %s: %s", session.session_id, e)
            logger.error(traceback.format_exc())
            if self.event_callback:
                self.event_callback({
                    "type": "todo_dispatch_failed",
                    "session_id": session.session_id,
                    "timestamp": datetime.now().isoformat(),
                    "error": str(e)[:300],
                    "assignment_ids": claimed_assignment_ids,
                })
            if claimed_assignment_ids:
                try:
                    with connect_runtime_db(activity_db_path) as conn:
                        task_hub.finalize_assignments(
                            conn,
                            assignment_ids=claimed_assignment_ids,
                            state="failed",
                            result_summary=f"todo_dispatch_failed:{str(e)[:180]}",
                            reopen_in_progress=True,
                            policy="todo",
                        )
                except Exception:
                    logger.exception("Failed to roll back claimed ToDo assignments for %s", session.session_id)
            
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
