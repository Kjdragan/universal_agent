import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Awaitable

from universal_agent.gateway import GatewaySession, GatewayRequest

logger = logging.getLogger(__name__)


def _event_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


_CODE_WORKFLOW_MARKERS = (
    "fix ",
    "debug",
    "refactor",
    "implement",
    "code change",
    "update the code",
    "update code",
    "write code",
    "repository",
    "repo",
    "typescript",
    "javascript",
    "python",
    "unit test",
    "test failure",
    "api route",
)

_RESEARCH_WORKFLOW_MARKERS = (
    "search for",
    "latest information",
    "latest developments",
    "research",
    "report",
    "analysis",
    "look up",
    "what happened",
    "pdf",
)


def infer_workflow_kind(
    *,
    user_input: str,
    delivery_mode: str,
    final_channel: str,
) -> str:
    """Infer the top-level workflow kind for one durable work item."""
    text = str(user_input or "").strip().lower()
    mode = str(delivery_mode or "").strip().lower()
    channel = str(final_channel or "").strip().lower() or "chat"

    if any(marker in text for marker in _CODE_WORKFLOW_MARKERS):
        return "code_change"
    if mode in {"standard_report", "enhanced_report"}:
        suffix = "chat" if channel == "chat" else "email"
        return f"research_report_{suffix}"
    if any(marker in text for marker in _RESEARCH_WORKFLOW_MARKERS):
        suffix = "chat" if channel == "chat" else "email"
        return f"research_report_{suffix}"
    if mode == "interactive_chat":
        return "interactive_answer"
    return "general_execution"


def build_execution_manifest(
    *,
    user_input: str,
    delivery_mode: str,
    final_channel: str,
    canonical_executor: str = "simone_first",
) -> dict[str, Any]:
    """Return the durable execution contract for a single work item."""
    workflow_kind = infer_workflow_kind(
        user_input=user_input,
        delivery_mode=delivery_mode,
        final_channel=final_channel,
    )
    text = str(user_input or "").strip().lower()
    mode = str(delivery_mode or "").strip().lower() or "standard_report"
    requires_pdf = mode in {"standard_report", "enhanced_report"} or "pdf" in text
    return {
        "workflow_kind": workflow_kind,
        "delivery_mode": mode,
        "requires_pdf": requires_pdf,
        "final_channel": str(final_channel or "chat").strip().lower() or "chat",
        "canonical_executor": str(canonical_executor or "simone_first").strip() or "simone_first",
    }


def resolve_execution_manifest(
    metadata: dict[str, Any] | None,
    *,
    fallback_description: str = "",
    final_channel: str = "",
    canonical_executor: str = "simone_first",
) -> dict[str, Any]:
    """Normalize the stored execution manifest for prompt/runtime use."""
    meta = metadata if isinstance(metadata, dict) else {}
    manifest = meta.get("workflow_manifest") if isinstance(meta.get("workflow_manifest"), dict) else {}
    if manifest:
        return {
            "workflow_kind": str(manifest.get("workflow_kind") or "general_execution").strip() or "general_execution",
            "delivery_mode": str(manifest.get("delivery_mode") or meta.get("delivery_mode") or "standard_report").strip() or "standard_report",
            "requires_pdf": bool(manifest.get("requires_pdf")),
            "final_channel": str(manifest.get("final_channel") or final_channel or "chat").strip().lower() or "chat",
            "canonical_executor": str(manifest.get("canonical_executor") or canonical_executor).strip() or canonical_executor,
        }
    return build_execution_manifest(
        user_input=fallback_description,
        delivery_mode=str(meta.get("delivery_mode") or "standard_report"),
        final_channel=final_channel or ("chat" if str(meta.get("delivery_mode") or "").strip() == "interactive_chat" else "email"),
        canonical_executor=canonical_executor,
    )

TODO_DISPATCH_PROMPT = """
You are Simone, the Pipeline Orchestrator. Your exclusive job is to execute the assigned work items from the Task Queue below to completion.
Do not perform any system monitoring, infrastructure checks, or background reporting.
The work items listed below are already claimed and routed into the canonical Task Hub execution lane.
Do not re-triage them, do not re-claim them, and do not stop or replace the active Task Hub assignment.
Your only goal is to execute the assigned work items, deliver results, then disposition them durably in Task Hub.

### Tool Constraints (CRITICAL):
- To interact with Task Hub (the durable work-item framework shown in the To Do List), strictly use `mcp__internal__task_hub_task_action`.
- You have expert knowledge of AgentMail from your skills, but during ToDo execution, you MUST STRICTLY use the wrapper tool `mcp__internal__send_agentmail` to send emails. This wrapper ensures final delivery is securely recorded in the Task Hub DB. DO NOT use `mcp__AgentMail__send_message` or Python/Bash scripts for emails here.
- NEVER write Python scripts, Bash scripts, or use `curl` to interact with Task Hub. Exclusively use the provided native MCP tools.
- Legacy external task-manager flows are retired. ALL missions are managed through Task Hub.
- You are the ONLY canonical executor for trusted email tasks and tracked interactive chat tasks. Hook sessions may triage and optionally send a short receipt acknowledgement, but they must not deliver the final report or final response.
- Internal execution steps may use Claude delegation (`Task` / `Agent`) when the execution manifest calls for sanctioned specialist work.
- Do NOT use `TaskStop` in this lane. It does not mutate Task Hub state and is never the right lifecycle primitive here.

### Execution Recovery (CRITICAL):
If a dependency or downstream execution path is unavailable, recover only with tools that are actually available in this run.
Do not invent fallback tools, do not assume Bash access, and do not force a delegation lane that the current task did not request.
If you believe a work item still needs a claim step, treat that as already satisfied and continue execution.
If the work item genuinely cannot proceed, disposition it via `mcp__internal__task_hub_task_action` with `review` or `block` and include the concrete missing dependency or system mismatch in the note.

After finishing work, ALWAYS disposition every claimed work item via `mcp__internal__task_hub_task_action` (`complete`, `review`, `block`, or `park`).
"""


def build_todo_execution_prompt(
    *,
    claimed_items: list[dict[str, Any]],
    capacity_snapshot_data: dict[str, Any] | None = None,
    active_assignments: list[dict[str, Any]] | None = None,
    origin_label: str = "",
) -> str:
    """Build the canonical Task Hub execution prompt."""
    lines = ["== TASK QUEUE =="]
    lines.append(f"You have {len(claimed_items)} work item(s) to process.")
    if origin_label:
        lines.append(f"Origin: {origin_label}")
    lines.append("")

    delivery_modes = sorted(
        {
            str(
                (
                    item.get("metadata")
                    if isinstance(item.get("metadata"), dict)
                    else {}
                ).get("delivery_mode")
                or "standard_report"
            ).strip()
            for item in claimed_items
        }
    )
    lines.append("== DELIVERY CONTRACT ==")
    lines.append(
        "delivery_modes={modes}".format(
            modes=", ".join(delivery_modes) if delivery_modes else "standard_report"
        )
    )
    lines.append(
        "For standard_report and enhanced_report: send exactly one final email with an executive summary in the body and attach the full report artifact when available."
    )
    lines.append(
        "For fast_summary: send exactly one concise body-only final email unless the task is explicitly upgraded."
    )
    lines.append(
        "For interactive_chat: deliver the final answer in this chat session and do not send email unless the user explicitly asked for email delivery."
    )
    lines.append("")
    lines.append("== CAPACITY SNAPSHOT ==")
    snapshot = capacity_snapshot_data or {}
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
    for idx, item in enumerate(claimed_items, 1):
        t_id = str(item.get("task_id") or "")
        title = str(item.get("title") or "(untitled)")
        desc = str(item.get("description") or "").strip()
        lines.append(f"Work Item {idx}: [{t_id}] {title}")
        lines.append(f"Description: {desc}")
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        delivery_mode = str(metadata.get("delivery_mode") or "standard_report").strip()
        manifest = resolve_execution_manifest(
            metadata,
            fallback_description=desc,
            final_channel="chat" if delivery_mode == "interactive_chat" else "email",
        )
        lines.append("== EXECUTION MANIFEST ==")
        lines.append(f"workflow_kind={manifest['workflow_kind']}")
        lines.append(f"Delivery Mode: {delivery_mode}")
        lines.append(f"requires_pdf={str(bool(manifest['requires_pdf'])).lower()}")
        lines.append(f"final_channel={manifest['final_channel']}")
        lines.append(f"canonical_executor={manifest['canonical_executor']}")
        routing = item.get("_routing") if isinstance(item.get("_routing"), dict) else {}
        if routing:
            lines.append(f"Routing Hint: {routing}")
        lines.append("")
    return f"{TODO_DISPATCH_PROMPT}\n\n" + "\n".join(lines)


class ToDoDispatchService:
    def __init__(
        self,
        execution_callback: Optional[Callable[[str, GatewayRequest], Awaitable[dict[str, Any]]]] = None,
        event_callback=None,
    ):
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.active_sessions: Dict[str, GatewaySession] = {}
        self.busy_sessions: set[str] = set()
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
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        session_role = str(metadata.get("session_role") or "").strip().lower()
        if session_role not in {"todo_execution", "todo"}:
            logger.debug("Skipping session %s for todo_dispatch (session_role=%s)", session.session_id, session_role or "none")
            return
        logger.info(f"Registering session {session.session_id} for todo_dispatch")
        self.active_sessions[session.session_id] = session
        if self.event_callback:
            self.event_callback({
                "type": "todo_dispatch_session_registered",
                "session_id": session.session_id,
                "timestamp": _event_timestamp(),
                "wake_pending": session.session_id in self.wake_sessions,
            })

    def unregister_session(self, session_id: str):
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
        if self.event_callback:
            self.event_callback({
                "type": "todo_dispatch_session_unregistered",
                "session_id": session_id,
                "timestamp": _event_timestamp(),
            })

    def request_dispatch_now(self, session_id: str) -> None:
        self.wake_sessions.add(session_id)
        if self.event_callback:
            self.event_callback({
                "type": "todo_dispatch_wake_requested",
                "session_id": session_id,
                "timestamp": _event_timestamp(),
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
                        "timestamp": _event_timestamp(),
                        "reason": capacity_reason,
                    })
                logger.info(
                    "ToDo dispatch deferred for %s: %s",
                    session.session_id,
                    capacity_reason,
                )
                return

            # ── Run-per-task: claim up to 5 tasks, each with its own workspace ──
            from universal_agent.services.execution_run_service import (
                allocate_execution_run,
            )
            all_claimed: list[dict] = []
            max_per_sweep = 5
            with connect_runtime_db(activity_db_path) as conn:
                for _ in range(max_per_sweep):
                    # Claim one task at a time with deferred workspace
                    batch = dispatch_sweep(
                        conn,
                        agent_id=f"todo:{session.session_id}",
                        limit=1,
                        provider_session_id=session.session_id,
                        workspace_dir=None,
                    )
                    if not batch:
                        break
                    item = batch[0]
                    task_id_val = str(item.get("task_id") or "").strip()

                    # Allocate a fresh run workspace for this specific task
                    run_ctx = allocate_execution_run(
                        task_id=task_id_val,
                        origin="todo_dispatch",
                        provider_session_id=session.session_id,
                        run_kind="todo_execution",
                        trigger_source="todo_dispatch",
                    )

                    # Stamp the assignment with run-scoped lineage
                    assignment_id = str(item.get("assignment_id") or "").strip()
                    if assignment_id:
                        task_hub.update_assignment_lineage(
                            conn,
                            assignment_id=assignment_id,
                            workflow_run_id=run_ctx.run_id,
                            workspace_dir=run_ctx.workspace_dir,
                        )
                    item["workspace_dir"] = run_ctx.workspace_dir
                    item["workflow_run_id"] = run_ctx.run_id
                    all_claimed.append(item)

            task_hub_claimed = all_claimed
            
            if not task_hub_claimed:
                if self.event_callback:
                    self.event_callback({
                        "type": "todo_dispatch_no_tasks",
                        "session_id": session.session_id,
                        "timestamp": _event_timestamp(),
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
                    "timestamp": _event_timestamp(),
                    "task_ids": task_ids,
                    "assignment_ids": claimed_assignment_ids,
                    "task_count": len(task_ids),
                })

            snapshot = capacity_snapshot()
            with connect_runtime_db(activity_db_path) as conn:
                activity = task_hub.get_agent_activity(conn)
            active_assignments = activity.get("active_assignments") if isinstance(activity, dict) else []
            prompt = build_todo_execution_prompt(
                claimed_items=task_hub_claimed,
                capacity_snapshot_data=snapshot,
                active_assignments=active_assignments,
                origin_label="todo_dispatcher",
            )

            # Provide visibility of progress specifically for To-Do List Tab UI
            if self.event_callback:
                self.event_callback({
                    "type": "agent_state_changed",
                    "session_id": session.session_id,
                    "event": {
                        "state": "processing",
                        "source": "todo_dispatcher",
                        "timestamp": _event_timestamp()
                    }
                })

            req = GatewayRequest(
                user_input=prompt,
                force_complex=True,
                metadata={
                    "source": "todo_dispatcher",
                    "run_kind": "todo_execution",
                    "dispatch_kind": "todo",
                    "claimed_task_ids": task_ids,
                    "claimed_assignment_ids": claimed_assignment_ids,
                    # ── Run-per-task lineage (first task's run) ──
                    "workflow_run_id": str(task_hub_claimed[0].get("workflow_run_id") or "").strip() if task_hub_claimed else "",
                    "workspace_dir": str(task_hub_claimed[0].get("workspace_dir") or "").strip() if task_hub_claimed else "",
                },
            )
            
            if self.execution_callback:
                dispatch_result = await self.execution_callback(session.session_id, req)
                decision = str((dispatch_result or {}).get("decision") or "accepted").strip().lower()
                if self.event_callback:
                    self.event_callback({
                        "type": "todo_dispatch_submitted",
                        "session_id": session.session_id,
                        "timestamp": _event_timestamp(),
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
                    "timestamp": _event_timestamp(),
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
                        "timestamp": _event_timestamp()
                    }
                })
