import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Awaitable

from universal_agent.gateway import GatewaySession, GatewayRequest
from universal_agent.codebase_policy import approved_codebase_roots_from_env

logger = logging.getLogger(__name__)

TODO_DISPATCH_MAX_PER_SWEEP = max(
    1,
    min(5, int(os.getenv("UA_TODO_DISPATCH_MAX_PER_SWEEP", "1") or 1)),
)


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


def _coerce_labels(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


def _env_positive_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except Exception:
        return default


def _vp_active_counts(active_assignments: list[dict[str, Any]] | None) -> tuple[int, int]:
    coder = 0
    general = 0
    for assignment in active_assignments or []:
        haystack = " ".join(
            str(assignment.get(key) or "")
            for key in ("agent_id", "provider_session_id", "title", "task_id")
        ).lower()
        if "vp.coder" in haystack or "codie" in haystack or "coder" in haystack:
            coder += 1
        elif "vp.general" in haystack or "atlas" in haystack:
            general += 1
    return coder, general


def _available_agents_for_llm_routing(
    active_assignments: list[dict[str, Any]] | None,
) -> frozenset[str]:
    from universal_agent.services.agent_router import AGENT_CODER, AGENT_GENERAL, AGENT_SIMONE

    active_coder, active_general = _vp_active_counts(active_assignments)
    max_coder = _env_positive_int("UA_MAX_CONCURRENT_VP_CODER", 1)
    max_general = _env_positive_int("UA_MAX_CONCURRENT_VP_GENERAL", 2)
    available = {AGENT_SIMONE}
    if active_coder < max_coder:
        available.add(AGENT_CODER)
    if active_general < max_general:
        available.add(AGENT_GENERAL)
    return frozenset(available)


def _non_coder_workflow_kind(
    *,
    description: str,
    delivery_mode: str,
    final_channel: str,
) -> str:
    text = str(description or "").strip().lower()
    mode = str(delivery_mode or "").strip().lower()
    channel = str(final_channel or "").strip().lower() or "chat"
    suffix = "chat" if channel == "chat" else "email"

    if mode == "interactive_email":
        return "interactive_answer_email"
    if mode in {"standard_report", "enhanced_report"}:
        return f"research_report_{suffix}"
    if any(marker in text for marker in _RESEARCH_WORKFLOW_MARKERS):
        return f"research_report_{suffix}"
    if mode == "interactive_chat":
        return "interactive_answer"
    return "general_execution"


def _reconcile_manifest_with_llm_route(task: dict[str, Any], route: dict[str, Any]) -> None:
    if str(route.get("method") or "").strip().lower() != "llm":
        return

    from universal_agent.services.agent_router import AGENT_CODER

    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    manifest = metadata.get("workflow_manifest") if isinstance(metadata.get("workflow_manifest"), dict) else {}
    if not manifest:
        return

    agent_id = str(route.get("agent_id") or "").strip()
    current_kind = str(manifest.get("workflow_kind") or "").strip()
    final_channel = str(manifest.get("final_channel") or "").strip().lower() or "chat"
    delivery_mode = str(manifest.get("delivery_mode") or metadata.get("delivery_mode") or "").strip()

    updated = dict(manifest)
    updated["llm_agent_route"] = {
        "agent_id": agent_id,
        "confidence": str(route.get("confidence") or "").strip(),
        "reasoning": str(route.get("reasoning") or route.get("reason") or "").strip(),
    }

    if agent_id == AGENT_CODER:
        updated["workflow_kind"] = "code_change"
        approved_roots = approved_codebase_roots_from_env()
        resolved_codebase_root = str(updated.get("codebase_root") or (approved_roots[0] if approved_roots else "")).strip()
        updated["codebase_root"] = resolved_codebase_root
        updated["repo_mutation_allowed"] = bool(resolved_codebase_root)
    elif current_kind == "code_change":
        updated["workflow_kind"] = _non_coder_workflow_kind(
            description=str(task.get("description") or ""),
            delivery_mode=delivery_mode,
            final_channel=final_channel,
        )
        updated["codebase_root"] = ""
        updated["repo_mutation_allowed"] = False

    metadata["workflow_manifest"] = updated
    task["metadata"] = metadata


async def _enrich_with_llm_agent_routing(
    claimed_items: list[dict[str, Any]],
    *,
    active_assignments: list[dict[str, Any]] | None = None,
) -> None:
    if not claimed_items:
        return

    from universal_agent.services.llm_classifier import classify_agent_route

    available_agents = _available_agents_for_llm_routing(active_assignments)
    for item in claimed_items:
        try:
            route = await classify_agent_route(
                title=str(item.get("title") or ""),
                description=str(item.get("description") or ""),
                labels=_coerce_labels(item.get("labels")),
                source_kind=str(item.get("source_kind") or ""),
                project_key=str(item.get("project_key") or ""),
                available_agents=available_agents,
            )
        except Exception as exc:
            logger.warning("LLM agent routing enrichment failed for task %s: %s", item.get("task_id"), exc)
            continue

        item["_routing"] = {
            "agent_id": str(route.get("agent_id") or "simone").strip() or "simone",
            "confidence": str(route.get("confidence") or "medium").strip() or "medium",
            "reason": str(route.get("reasoning") or route.get("reason") or "").strip(),
            "method": str(route.get("method") or "llm").strip() or "llm",
            "should_delegate": bool(route.get("should_delegate")),
        }
        _reconcile_manifest_with_llm_route(item, route)


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
    if mode == "interactive_email":
        return "interactive_answer_email"
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
    codebase_root: str | None = None,
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
    approved_roots = approved_codebase_roots_from_env()
    resolved_codebase_root = ""
    if workflow_kind == "code_change":
        resolved_codebase_root = str(codebase_root or (approved_roots[0] if approved_roots else "")).strip()
    return {
        "workflow_kind": workflow_kind,
        "delivery_mode": mode,
        "requires_pdf": requires_pdf,
        "final_channel": str(final_channel or "chat").strip().lower() or "chat",
        "canonical_executor": str(canonical_executor or "simone_first").strip() or "simone_first",
        "codebase_root": resolved_codebase_root,
        "repo_mutation_allowed": bool(workflow_kind == "code_change" and resolved_codebase_root),
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
            "codebase_root": str(manifest.get("codebase_root") or meta.get("codebase_root") or "").strip(),
            "repo_mutation_allowed": bool(manifest.get("repo_mutation_allowed")),
        }
    return build_execution_manifest(
        user_input=fallback_description,
        delivery_mode=str(meta.get("delivery_mode") or "standard_report"),
        final_channel=final_channel or ("chat" if str(meta.get("delivery_mode") or "").strip() == "interactive_chat" else "email"),
        canonical_executor=canonical_executor,
        codebase_root=str(meta.get("codebase_root") or "").strip() or None,
    )

TODO_DISPATCH_PROMPT = """
You are Simone, the Pipeline Orchestrator. Your exclusive job is to execute the assigned work items from the Task Queue below to completion.
Do not perform any system monitoring, infrastructure checks, or background reporting.
The work items listed below are already claimed and routed into the canonical Task Hub execution lane.
Do not re-claim them, and do not stop or replace the active Task Hub assignment.
Use the LLM routing judgment attached to each item as advisory guidance for whether to execute yourself or delegate to CODIE/ATLAS.
Your only goal is to execute the assigned work items, deliver results, then disposition them durably in Task Hub.

### Tool Constraints (CRITICAL):
- To interact with Task Hub (the durable work-item framework shown in the To Do List), strictly use `task_hub_task_action`.
- You have expert knowledge of AgentMail from your skills. During ToDo execution, use the official AgentMail MCP tools for outbound delivery: `mcp__agentmail__send_message` for new messages and `mcp__agentmail__reply_to_message` for replies. You must specify the sending inbox in the `inboxId` param (Simone's inbox is `oddcity216@agentmail.to`). For local attachments, first call `prepare_agentmail_attachment` and pass the returned object in the MCP tool's `attachments` field. Do NOT use Python/Bash scripts or CLI commands for email here.
- NEVER write Python scripts, Bash scripts, or use `curl` to interact with Task Hub. Exclusively use the provided native MCP tools.
- Legacy external task-manager flows are retired. ALL missions are managed through Task Hub.
- You are the ONLY canonical executor for trusted email tasks and tracked interactive chat tasks. Hook sessions may triage and optionally send a short receipt acknowledgement, but they must not deliver the final report or final response.
- Internal execution steps may use Claude delegation (`Task` / `Agent`) when the execution manifest calls for sanctioned specialist work.
- Do NOT use `TaskStop` in this lane. It does not mutate Task Hub state and is never the right lifecycle primitive here.
- When delegating work via `vp_dispatch_mission`, you MUST provide an `idempotency_key` (e.g. `task-<task_id>`) to prevent duplicate dispatches if execution is interrupted.

### Execution Recovery (CRITICAL):
If a dependency or downstream execution path is unavailable, recover only with tools that are actually available in this run.
Do not invent fallback tools, do not assume Bash access, and do not force a delegation lane that the current task did not request.
If you believe a work item still needs a claim step, treat that as already satisfied and continue execution.
If the work item genuinely cannot proceed, disposition it via `task_hub_task_action` with `review` or `block` and include the concrete missing dependency or system mismatch in the note.

After finishing work, ALWAYS disposition every claimed work item via `task_hub_task_action` (`complete`, `review`, `block`, or `park`).
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
    mode_set = set(delivery_modes or ["standard_report"])
    if mode_set & {"standard_report", "enhanced_report"}:
        lines.append(
            "For standard_report and enhanced_report: send exactly one single final email with a natural, friendly introduction. Attach both the generated HTML report and the PDF report using the `attachments` array. Do not put the full report content in the email body."
        )
    if "fast_summary" in mode_set:
        lines.append(
            "For fast_summary: send exactly one concise body-only final email unless the task is explicitly upgraded."
        )
    if "interactive_email" in mode_set:
        lines.append(
            "For interactive_email: send exactly one direct final email aligned to the user's request. Do not prepend an executive summary unless the user explicitly asked for one."
        )
    if "interactive_chat" in mode_set:
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
                "- [Task ID: {task_id}] (Agent: {agent}) - {title}".format(
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
        if str(manifest.get("codebase_root") or "").strip():
            lines.append(f"codebase_root={manifest['codebase_root']}")
            lines.append(
                f"repo_mutation_allowed={str(bool(manifest.get('repo_mutation_allowed'))).lower()}"
            )
            lines.append(
                "Repo-backed coding is authorized for this work item. Edit repo files under "
                "`CURRENT_CODEBASE_ROOT`, but keep generated artifacts in `CURRENT_RUN_WORKSPACE`."
            )
        routing = item.get("_routing") if isinstance(item.get("_routing"), dict) else {}
        if routing:
            label = "LLM Routing Judgment" if routing.get("method") == "llm" else "Routing Hint"
            lines.append(f"{label}: {routing}")
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

            # ── Run-per-task: claim a bounded number of tasks, each with its own workspace ──
            from universal_agent.services.execution_run_service import (
                allocate_execution_run,
            )
            all_claimed: list[dict] = []
            max_per_sweep = TODO_DISPATCH_MAX_PER_SWEEP
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
            await _enrich_with_llm_agent_routing(
                task_hub_claimed,
                active_assignments=active_assignments,
            )
            prompt = build_todo_execution_prompt(
                claimed_items=task_hub_claimed,
                capacity_snapshot_data=snapshot,
                active_assignments=active_assignments,
                origin_label="todo_dispatcher",
            )
            manifest_roots = sorted(
                {
                    str(
                        (
                            (
                                item.get("metadata")
                                if isinstance(item.get("metadata"), dict)
                                else {}
                            ).get("workflow_manifest")
                            if isinstance(
                                (
                                    item.get("metadata")
                                    if isinstance(item.get("metadata"), dict)
                                    else {}
                                ).get("workflow_manifest"),
                                dict,
                            )
                            else {}
                        ).get("codebase_root")
                        or ""
                    ).strip()
                    for item in task_hub_claimed
                }
                - {""}
            )
            manifest_workflow_kinds = sorted(
                {
                    str(
                        (
                            (
                                item.get("metadata")
                                if isinstance(item.get("metadata"), dict)
                                else {}
                            ).get("workflow_manifest")
                            if isinstance(
                                (
                                    item.get("metadata")
                                    if isinstance(item.get("metadata"), dict)
                                    else {}
                                ).get("workflow_manifest"),
                                dict,
                            )
                            else {}
                        ).get("workflow_kind")
                        or ""
                    ).strip()
                    for item in task_hub_claimed
                }
                - {""}
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
                    "workflow_kind": manifest_workflow_kinds[0] if len(manifest_workflow_kinds) == 1 else "",
                    "codebase_root": manifest_roots[0] if len(manifest_roots) == 1 else "",
                    "repo_mutation_allowed": bool(len(manifest_roots) == 1 and manifest_roots[0]),
                    "allowed_codebase_roots": approved_codebase_roots_from_env(),
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

                # Ensure completed tasks get the proper active workspace links persisted
                if task_ids and task_hub_claimed:
                    try:
                        run_id = str(task_hub_claimed[0].get("workflow_run_id") or "")
                        wdir = str(task_hub_claimed[0].get("workspace_dir") or "")
                        if wdir:
                            with connect_runtime_db(activity_db_path) as conn:
                                for t_id in task_ids:
                                    item = task_hub.get_item(conn, t_id)
                                    if item and str(item.get("status") or "") == task_hub.TASK_STATUS_COMPLETED:
                                        metadata = dict(item.get("metadata") or {})
                                        dispatch_meta = dict(metadata.get("dispatch") or {})
                                        
                                        # If it exists, ensure the completion lineage holds it.
                                        if not dispatch_meta.get("last_workspace_dir"):
                                            dispatch_meta["last_workspace_dir"] = wdir
                                            if run_id:
                                                dispatch_meta["last_workflow_run_id"] = run_id
                                            metadata["dispatch"] = dispatch_meta
                                            conn.execute(
                                                "UPDATE task_hub_items SET metadata_json = ? WHERE task_id = ?",
                                                (json.dumps(metadata), t_id)
                                            )
                                            conn.commit()
                    except Exception as attach_e:
                        logger.error("Failed to attach completed workspace link for %s: %s", session.session_id, attach_e)
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
