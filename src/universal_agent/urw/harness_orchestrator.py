"""
URW Harness Orchestrator

Simplified orchestrator for massive requests that uses:
- Interview-generated Plan object
- HarnessSessionManager for phase session directories
- Multi-agent system as execution engine (via process_turn)
- evaluation-judge sub-agent for phase verification
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .plan_schema import Plan, Phase, AtomicTask, TaskStatus as PlanTaskStatus
from .plan_persistence import PlanPersistence, SQLitePlanStore
from .harness_session import HarnessSessionManager
from .harness_helpers import (
    toggle_session,
    compact_agent_context,
    build_harness_context_injection,
)
from .interview import run_planning_interview, run_planning_from_template
from .evaluator import CompositeEvaluator, EvaluationResult, create_default_evaluator
from .adapter import HarnessAdapter
from .state import Artifact, ArtifactType, Task as StateTask
from universal_agent.execution_context import bind_workspace_env
import uuid

# Gateway imports for Stage 5
try:
    from universal_agent.gateway import Gateway, InProcessGateway, ExternalGateway, GatewayRequest
    from universal_agent.agent_core import AgentEvent, EventType
    GATEWAY_AVAILABLE = True
except ImportError:
    GATEWAY_AVAILABLE = False
    Gateway = None  # type: ignore
    InProcessGateway = None  # type: ignore
    ExternalGateway = None  # type: ignore
    GatewayRequest = None  # type: ignore
    AgentEvent = None  # type: ignore
    EventType = None  # type: ignore


class HarnessStatus(Enum):
    """Status of the harness orchestration."""
    IDLE = "idle"
    INTERVIEWING = "interviewing"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class HarnessConfig:
    """Configuration for harness orchestration."""
    max_retry_per_phase: int = 3
    force_new_client_between_phases: bool = False
    persist_to_sqlite: bool = True
    verbose: bool = True
    max_iterations: int = 20
    # Stage 5: Gateway integration
    use_gateway: bool = False
    gateway_url: Optional[str] = None  # If set, use external gateway


@dataclass
class PhaseResult:
    """Result of a phase execution."""
    phase_id: str
    phase_name: str
    success: bool
    session_path: str
    artifacts_produced: List[str] = field(default_factory=list)
    agent_output: str = ""
    error: Optional[str] = None
    evaluation_notes: Optional[str] = None


class ProcessTurnInterface(Protocol):
    """Interface for the main agent's process_turn function."""
    async def __call__(
        self, 
        client: Any, 
        user_input: str, 
        workspace_dir: str,
        **kwargs
    ) -> Any:
        ...


class HarnessOrchestrator:
    """
    Orchestrates massive requests using Plan-based phase execution.
    
    Flow:
    1. Interview user → generate Plan
    2. For each phase in Plan:
       a. Toggle to new session directory
       b. Build phase prompt with context injection
       c. Feed to multi-agent system (process_turn or gateway)
       d. Evaluate completion
       e. Mark phase complete/retry
    3. Generate final summary
    """
    
    def __init__(
        self,
        workspaces_root: Path,
        config: Optional[HarnessConfig] = None,
    ):
        self.workspaces_root = Path(workspaces_root)
        self.config = config or HarnessConfig()
        
        # Initialize on first run
        self.session_manager: Optional[HarnessSessionManager] = None
        self.plan: Optional[Plan] = None
        self.status = HarnessStatus.IDLE
        self.phase_results: List[PhaseResult] = []
        
        # Persistence
        self.file_persistence: Optional[PlanPersistence] = None
        self.db_persistence: Optional[SQLitePlanStore] = None
        
        # Stage 5: Gateway integration
        self._gateway: Optional[Gateway] = None
        self._gateway_session: Optional[Any] = None

    async def _get_gateway(self) -> Optional[Gateway]:
        """Get or create gateway instance if gateway mode enabled."""
        if not self.config.use_gateway:
            return None
        if not GATEWAY_AVAILABLE:
            self._log("⚠️ Gateway not available, falling back to process_turn")
            return None
        if self._gateway is None:
            if self.config.gateway_url:
                self._gateway = ExternalGateway(base_url=self.config.gateway_url)
                self._log(f"🌐 Using external gateway: {self.config.gateway_url}")
            else:
                self._gateway = InProcessGateway()
                self._log("🌐 Using in-process gateway")
        return self._gateway

    async def _gateway_process_turn(
        self,
        prompt: str,
        workspace_dir: str,
    ) -> Dict[str, Any]:
        """Execute a turn through the gateway instead of process_turn."""
        gateway = await self._get_gateway()
        if not gateway:
            raise RuntimeError("Gateway not initialized")

        # Create/reuse session for this workspace
        if self._gateway_session is None or self._gateway_session.workspace_dir != workspace_dir:
            self._gateway_session = await gateway.create_session(
                user_id="urw_harness",
                workspace_dir=workspace_dir,
            )

        request = GatewayRequest(user_input=prompt)
        
        output_chunks = []
        tool_calls = []
        
        async for event in gateway.execute(self._gateway_session, request):
            if event.type == EventType.TEXT:
                text = event.data.get("text", "")
                if text:
                    output_chunks.append(text)
            elif event.type == EventType.TOOL_CALL:
                tool_calls.append(event.data)

        response_text = "".join(output_chunks)
        
        return {
            "response_text": response_text,
            "tool_calls": tool_calls,
        }
    
    def _log(self, message: str) -> None:
        """Log with timestamp."""
        if self.config.verbose:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[Harness {ts}] {message}", flush=True)
    
    async def run(
        self,
        massive_request: str,
        process_turn: ProcessTurnInterface,
        client: Any,
        agent: Any = None,
        skip_interview: bool = False,
        plan_file: Optional[Path] = None,
        template_file: Optional[Path] = None,
        harness_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the complete harness flow.
        
        Args:
            massive_request: The user's massive request
            process_turn: The main agent's process_turn function
            client: The ClaudeSDKClient to use (kept same for auto-compaction)
            skip_interview: If True, skip interview and use plan_file
            plan_file: Path to JSON plan file (used when skip_interview=True)
            template_file: Path to interview transcript JSON (for template-based planning)
            
        Returns:
            Summary dict with results
        """
        self._log(f"Starting harness for: {massive_request[:80]}...")
        
        # 1. Create harness directory and session manager
        self.session_manager = HarnessSessionManager(self.workspaces_root, harness_id=harness_id)
        harness_dir = self.session_manager.harness_dir
        harness_id = self.session_manager.harness_id
        
        self._log(f"Harness directory: {harness_dir}")
        
        # Setup persistence
        self.file_persistence = PlanPersistence(harness_dir)
        if self.config.persist_to_sqlite:
            db_path = str(harness_dir / "harness.db")
            self.db_persistence = SQLitePlanStore(db_path)
        
        # 2. Get Plan (from file, template, or interview)
        if skip_interview and plan_file:
            self.status = HarnessStatus.EXECUTING
            self._log(f"Skipping interview, loading plan from: {plan_file}")
            
            with open(plan_file, "r") as f:
                plan_data = json.load(f)
            self.plan = Plan.model_validate(plan_data)
            self._log(f"Loaded plan: {self.plan.name}")
            
        elif template_file:
            self.status = HarnessStatus.INTERVIEWING
            self._log(f"Generating plan from template: {template_file}")
            
            self.plan = await run_planning_from_template(
                harness_dir=harness_dir,
                harness_id=harness_id,
                template_path=template_file,
            )
            
        else:
            # Run interview to generate Plan
            self.status = HarnessStatus.INTERVIEWING
            self._log("Starting planning interview...")
            
            self.plan = await run_planning_interview(
                harness_dir=harness_dir,
                harness_id=harness_id,
                massive_request=massive_request,
            )
        
        if not self.plan:
            self.status = HarnessStatus.FAILED
            return {"status": "failed", "error": "Interview failed to produce a plan"}
        
        # Set phases in session manager
        self.session_manager.set_phases([{"title": p.name} for p in self.plan.phases])
        
        # Persist the plan
        self._persist_plan()
        self._log(f"Plan created: {len(self.plan.phases)} phases, {self.plan.total_tasks()} tasks")
        
        # 3. Execute each phase
        self.status = HarnessStatus.EXECUTING
        
        # 3. Execute each phase
        self.status = HarnessStatus.EXECUTING
        
        for phase in self.plan.phases:
            # Check if using resume and phase is already done
            if phase.status == "completed":
                self._log(f"Skipping completed phase: {phase.name}")
                # Re-populate result for summary
                self.phase_results.append(PhaseResult(
                    phase_id=str(phase.id),
                    phase_name=phase.name,
                    success=True,
                    session_path=phase.session_path or "restored_from_plan",
                    evaluation_notes="Skipped (Already Complete)"
                ))
                continue

            # Check if resuming interrupt
            is_resuming = (phase.status == PlanTaskStatus.IN_PROGRESS)
            
            # Mark as IN_PROGRESS
            if phase.status != PlanTaskStatus.IN_PROGRESS:
                phase.status = PlanTaskStatus.IN_PROGRESS
                self._persist_plan()

            # --- Verification Loop (Ralph Loop) ---
            phase_success = False
            phase_result = None
            retry_count = 0
            feedback = None
            
            while retry_count <= self.config.max_retry_per_phase:
                if retry_count > 0:
                    self._log(f"Retry {retry_count}/{self.config.max_retry_per_phase} for Phase {phase.name}")
                
                # Execute
                prior_session_path = phase_result.session_path if phase_result else None
                result = await self._execute_phase(
                    phase,
                    process_turn,
                    client,
                    agent=agent,
                    feedback=feedback,
                    is_resuming=is_resuming,
                    prior_session_path=prior_session_path,
                )
                
                # Only "resuming" on the very first try of the phase. 
                # If we loop (retry), it's not "resuming" anymore, it's "retrying".
                is_resuming = False
                
                # Evaluate
                eval_result = await self._evaluate_phase(
                    phase,
                    result.session_path,
                    client,
                    result.artifacts_produced,
                    result.agent_output,
                )
                
                # Check Completion
                if eval_result.is_complete:
                    self._log(f"Phase {phase.name} PASSED verification! Score: {eval_result.overall_score:.2f}")
                    result.success = True
                    result.evaluation_notes = f"Passed with score {eval_result.overall_score:.2f}"
                    phase_success = True
                    phase_result = result
                    break
                else:
                    self._log(f"Phase {phase.name} FAILED verification. Score: {eval_result.overall_score:.2f}")
                    self._log(f"Missing: {eval_result.missing_elements}")
                    
                    # Prepare feedback for next iteration
                    retry_count += 1
                    feedback = f"""
PREVIOUS RETRY FAILED VERIFICATION.
ISSUES TO FIX:
{chr(10).join(f"- {m}" for m in eval_result.missing_elements)}

SUGGESTED ACTIONS:
{chr(10).join(f"- {s}" for s in eval_result.suggested_actions)}

PLEASE FIX THESE ISSUES AND RE-SUBMIT ARTIFACTS.
"""
                    result.success = False
                    result.error = f"Verification failed: {eval_result.missing_elements}"
                    phase_result = result # Keep last result
            
            # End Loop
            
            self.phase_results.append(phase_result)
            
            if phase_success:
                self.plan.mark_phase_complete(phase.id)
                self._persist_plan()
            else:
                self._log(f"Phase {phase.name} failed after {retry_count} attempts.")
                if not await self._handle_phase_failure(phase, phase_result):
                    self.status = HarnessStatus.FAILED
                    break
        
        # 4. Generate summary
        if self.plan.status == PlanTaskStatus.COMPLETED:
            self.status = HarnessStatus.COMPLETE
            self._log("✅ All phases complete!")
        
        return self._generate_summary()
    
    async def _execute_phase(
        self,
        phase: Phase,
        process_turn: ProcessTurnInterface,
        client: Any,
        agent: Any = None,
        feedback: Optional[str] = None,
        is_resuming: bool = False,
        prior_session_path: Optional[str] = None,
    ) -> PhaseResult:
        """
        Execute a single phase of the plan.
        """
        self._log(f"=== Phase {phase.order + 1}: {phase.name} ===")
        
        # 1. Toggle to new session directory
        session_path = self.session_manager.next_phase_session()
        phase.session_path = session_path
        
        # CRITICAL: Update env var so MCP tools (mcp_server.py) write to the correct phase dir
        bind_workspace_env(str(session_path))

        # EFFICIENCY: Pre-create standard directories to prevent agent 404s
        (session_path / "work_products").mkdir(exist_ok=True)
        (session_path / "tasks").mkdir(exist_ok=True)
        (session_path / "search_results").mkdir(exist_ok=True)
        (session_path / "downloads").mkdir(exist_ok=True)
        self._persist_current_workspace(session_path)

        if prior_session_path:
            self._log(f"Bootstrapping retry workspace from: {prior_session_path}")
            self._bootstrap_retry_workspace(Path(prior_session_path), session_path)
        
        self._log(f"Session: {session_path}")
        
        # 2. Check compaction strategy (if applicable context needs carryover)
        from universal_agent.urw.harness_helpers import compact_agent_context
        compact_result = compact_agent_context(client, self.config.force_new_client_between_phases)
        self._log(f"Context: {compact_result['notes']}")
        
        # DEBUG: Check agent status
        self._log(f"DEBUG: agent_object={agent}, has_history={hasattr(agent, 'history') if agent else 'N/A'}")

        if not compact_result.get("keep_client", True):
            # Try to clear history on the underlying UniversalAgent if available
            if agent and hasattr(agent, "history"):
                if hasattr(agent.history, "reset"):
                     agent.history.reset()
                     self._log("🧼 Agent history reset (Phase Boundary Hard Reset)")
                else:
                     self._log("⚠️ Could not reset agent history: .reset() missing")
            # Fallback for SDK client history (legacy support)
            elif hasattr(client, "history"):
                cleared = False
                hist = client.history
                
                if hasattr(hist, "clear_history"):
                    hist.clear_history()
                    cleared = True
                elif hasattr(hist, "reset"): # Support for MessageHistory
                    hist.reset()
                    cleared = True
                else:
                    client.history = []
                    cleared = True
                    
                if cleared:
                    self._log("🧼 Client history cleared (Phase Boundary Hard Reset)")
            else:
                self._log("⚠️ Could not clear history: agent/client history not available")
        
        # --- Heuristic Context Injection ---
        # "Prod" the agent to use specialists if the task description matches known capabilities.
        # This complements the dynamic registry in agent_core.py.
        context_hints = []
        phase_text = (phase.name + " " + " ".join([t.description or "" for t in phase.tasks])).lower()
        
        if any(kw in phase_text for kw in ["report", "html", "summary", "document", "write"]):
            context_hints.append(
                "💡 **Collaboration Hint**: This phase appears to involve report generation. "
                "Remember to delegate to the `report-writer` sub-agent for drafting and refined HTML output."
            )
        
        if any(kw in phase_text for kw in ["research", "investigate", "find", "search", "gather"]):
            context_hints.append(
                "💡 **Collaboration Hint**: This phase appears to involve research. "
                "Delegate deep searches to the `research-specialist` sub-agent."
            )
            
        hint_block = ""
        if context_hints:
            hint_block = "\n\n" + "\n".join(context_hints)
        
        # Build prompt
        task_list = "\n".join([f"- {t.name}: {t.description}" for t in phase.tasks])
        prompt = (
            f"# Phase {phase.order} of {len(self.plan.phases)}: {phase.name}\n\n"
            f"You are working through a larger multi-phase project. Your goal for this session is to complete Phase {phase.order}.\n\n"
            f"## Phase Objectives\n{task_list}\n\n"
            f"## Instructions\n"
            f"1. execute the tasks for this phase sequentially.\n"
            f"2. Use the `Task` tool to delegate sub-components to specialists (see Available Specialists).{hint_block}\n"
            f"3. When finished, call `notify_user` to signal completion.\n"
        )
        prior_sessions = self.session_manager.get_prior_session_paths()
        phase_prompt = build_harness_context_injection(
            phase_num=phase.order + 1,  # If order is 0-indexed in schema but 1-indexed from inputs.. wait use session manager
            total_phases=len(self.plan.phases),
            phase_title=phase.name,
            phase_instructions=phase.prompt or phase.description,
            prior_session_paths=prior_sessions,
            expected_artifacts=phase.get_expected_artifacts(),
            tasks=phase.tasks,
            current_session_path=session_path,
        )
        
        if is_resuming:
            self._log(f"Resuming phase {phase.name} (was IN_PROGRESS)")
            phase_prompt = (
                f"# 🔄 RESUMING INTERRUPTED PHASE {phase.order + 1}: {phase.name}\n\n"
                f"**IMPORTANT**: You were working on this phase but the system was interrupted/restarted.\n"
                f"Your previous work products are preserved in: `{session_path}`\n\n"
                f"**INSTRUCTIONS:**\n"
                f"1. Check the workspace files to see what you have already finished.\n"
                f"2. DO NOT redo work that is already complete (e.g. do not re-write files if they exist).\n"
                f"3. Continue from where you left off to complete the remaining tasks.\n\n"
                f"--- Original Phase Context below ---\n\n"
                f"{phase_prompt}"
            )
        
        if feedback:
            phase_prompt += f"\n\n# ⚠️ CRITICAL FEEDBACK FROM PREVIOUS ATTEMPT\n{feedback}\nYou must address these issues in this attempt."
        
        # 4. Execution Loop (Initial + Repairs)
        max_repairs = 3
        current_prompt = phase_prompt
        
        for attempt in range(max_repairs + 1):
            is_repair = attempt > 0
            if is_repair:
                self._log(f"🔧 Starting In-Session Repair Attempt {attempt}/{max_repairs}")
            
            try:
                # Execute Turn (via gateway or process_turn)
                if self.config.use_gateway and await self._get_gateway():
                    result = await self._gateway_process_turn(
                        current_prompt,
                        str(session_path),
                    )
                else:
                    result = await process_turn(
                        client,
                        current_prompt,
                        session_path,
                        max_iterations=self.config.max_iterations,
                    )
                
                # Capture output
                agent_output = ""
                if hasattr(result, "response_text"):
                    agent_output = result.response_text or ""
                elif isinstance(result, dict):
                    agent_output = str(result.get("response_text") or "")

                # Internal Verification
                artifacts_list = self._scan_session_artifacts(session_path)
                
                # Check for "soft" success (files exist)
                has_artifacts = len(artifacts_list) > 0
                
                # Run Proper Evaluation
                self._log(f"running internal verification for attempt {attempt}...")
                eval_result = await self._evaluate_phase(
                    phase,
                    session_path,
                    client,
                    artifacts_list,
                    agent_output
                )
                
                if eval_result.is_complete:
                    self._log("✅ Internal Verification Passed.")
                    return PhaseResult(
                        phase_id=str(phase.id),
                        phase_name=phase.name,
                        success=True,
                        session_path=session_path,
                        artifacts_produced=artifacts_list,
                        agent_output=agent_output,
                        evaluation_notes=f"Passed on attempt {attempt}"
                    )
                
                # If Failed: Prepare Repair Prompt
                if attempt < max_repairs:
                    missing = "\n".join([f"- {m}" for m in eval_result.missing_elements])
                    current_prompt = (
                        f"User: 🛑 Verification Failed.\n\n"
                        f"The System verification found the following missing elements:\n{missing}\n\n"
                        f"SUGGESTIONS:\n{chr(10).join([f'- {s}' for s in eval_result.suggested_actions])}\n\n"
                        f"INSTRUCTIONS:\n"
                        f"1. Review the feedback above.\n"
                        f"2. Fix the issues in the artifacts.\n"
                        f"3. When done, call notify_user again."
                    )
                    self._log(f"❌ Verification Failed. Triggering repair loop.")
                    continue
                
                # If max attempts reached, return failure
                self._log("❌ Max in-session repairs reached. Returning failure.")
                return PhaseResult(
                    phase_id=str(phase.id),
                    phase_name=phase.name,
                    success=False,
                    session_path=session_path,
                    artifacts_produced=artifacts_list,
                    error=f"Failed after {attempt} repairs. Missing: {eval_result.missing_elements}",
                    agent_output=agent_output,
                )

            except Exception as e:
                self._log(f"Phase execution error on attempt {attempt}: {e}")
                # If critical error, we might stop or try to repair?
                # For now, treat exception as fatal for this attempt, but maybe retry?
                # safest is to return failure and let outer loop handle 'crash' retries
                return PhaseResult(
                    phase_id=str(phase.id),
                    phase_name=phase.name,
                    success=False,
                    session_path=session_path,
                    error=str(e),
                )
        
        # Fallback (should be unreachable due to return in loop)
        return PhaseResult(
            phase_id=str(phase.id),
            phase_name=phase.name,
            success=False,
            session_path=session_path,
            error="Loop exhausted",
        )
    
    def _scan_session_artifacts(self, session_path: str) -> List[str]:
        """Scan session directory for produced artifacts."""
        artifacts = []
        session_root = Path(session_path)
        work_products = session_root / "work_products"
        tasks_root = session_root / "tasks"
        
        if work_products.exists():
            for f in work_products.rglob("*"):
                if f.is_file():
                    artifacts.append(str(f.relative_to(session_path)))

        # Include top-level task outputs (avoid deep filtered corpora)
        if tasks_root.exists():
            for task_dir in tasks_root.iterdir():
                if not task_dir.is_dir():
                    continue
                for f in task_dir.iterdir():
                    if f.is_file():
                        artifacts.append(str(f.relative_to(session_path)))

        # CRITICAL FIX: Include search results so research tasks can be verified
        search_results = session_root / "search_results"
        if search_results.exists():
            for f in search_results.rglob("*"):
                if f.is_file() and f.suffix.lower() == ".json":
                    artifacts.append(str(f.relative_to(session_path)))
        
        return artifacts

    def _bootstrap_retry_workspace(self, previous_path: Path, session_path: Path) -> None:
        """Copy prior attempt artifacts to avoid redoing completed work."""
        if not previous_path.exists():
            return

        for subdir in ("work_products", "tasks", "search_results"):
            src = previous_path / subdir
            dst = session_path / subdir
            if not src.exists():
                continue
            try:
                shutil.copytree(src, dst, dirs_exist_ok=True)
            except Exception as exc:
                self._log(f"⚠️ Failed to copy {subdir}: {exc}")

    def _persist_current_workspace(self, session_path: Path) -> None:
        """Persist current phase workspace for tools that cannot see env updates."""
        try:
            marker_path = self.workspaces_root / ".current_session_workspace"
            marker_path.write_text(str(session_path))
        except Exception as exc:
            self._log(f"⚠️ Failed to persist current workspace: {exc}")

    async def _evaluate_phase(
        self,
        phase: Phase,
        session_path: str,
        client: Any,
        artifact_paths: List[str],
        agent_output: str,
    ) -> EvaluationResult:
        """Evaluate a phase's completion using CompositeEvaluator."""
        self._log("Running verification...")
        
        # 1. Setup Evaluator
        evaluator = create_default_evaluator(client)
        
        # 2. Convert Artifacts
        artifacts = []
        for path_str in artifact_paths:
             artifacts.append(Artifact(
                 id=path_str, # Use path as ID for simplicity
                 task_id="unknown",
                 artifact_type=ArtifactType.FILE,
                 file_path=path_str
             ))
             
        # 3. Evaluate each Atomic Task
        # We aggregate results: All atomic tasks must pass
        
        failures = []
        suggestions = []
        total_score = 0
        task_count = 0
        
        # Special case: If no tasks defined, pass (Interview phase checks happen elsewhere)
        if not phase.tasks:
            return EvaluationResult(True, "high", 1.0)
            
        for atomic_task in phase.tasks:
            # Adapt to State Task
            state_task = HarnessAdapter.atomic_task_to_state_task(atomic_task)
            
            # Evaluate
            # output is shared across phase, so we pass empty string or assume artifacts cover it
            eval_context = self._build_eval_context(
                session_path=session_path,
                artifacts=artifacts,
                agent_output=agent_output,
            )

            result = evaluator.evaluate(
                task=state_task,
                artifacts=artifacts,
                agent_output=eval_context,
                workspace_path=Path(session_path)
            )
            
            task_count += 1
            total_score += result.overall_score
            
            if not result.is_complete:
                failures.append(f"Task '{atomic_task.name}': {', '.join(result.missing_elements)}")
                suggestions.extend(result.suggested_actions)
                self._log(f"  ❌ Task '{atomic_task.name}' failed: {result.missing_elements}")
            else:
                self._log(f"  ✅ Task '{atomic_task.name}' passed")
                
        # Aggregate
        is_success = len(failures) == 0
        avg_score = total_score / task_count if task_count else 0.0
        
        return EvaluationResult(
            is_complete=is_success,
            confidence="high", # Placeholder
            overall_score=avg_score,
            missing_elements=failures,
            suggested_actions=suggestions
        )

    def _build_eval_context(
        self,
        session_path: str,
        artifacts: List[Artifact],
        agent_output: str,
    ) -> str:
        summary_lines = ["ARTIFACTS SUMMARY:"]
        if artifacts:
            for art in artifacts:
                if art.file_path:
                    summary_lines.append(f"- {art.file_path}")
        else:
            summary_lines.append("- (none)")

        headings = self._build_html_headings(session_path, artifacts)
        snippets = self._build_html_section_snippets(session_path, artifacts)
        preview = self._build_artifact_previews(session_path, artifacts)

        parts = [
            "\n".join(summary_lines),
            headings,
            snippets,
            preview,
        ]
        if agent_output:
            parts.append("AGENT OUTPUT:\n" + agent_output)

        combined = "\n\n".join([p for p in parts if p])
        return combined[:35000]

    def _build_html_headings(self, session_path: str, artifacts: List[Artifact]) -> str:
        session_root = Path(session_path)
        headings_blocks: List[str] = []

        for art in artifacts:
            if not art.file_path or not art.file_path.lower().endswith(".html"):
                continue
            file_path = session_root / art.file_path
            if not file_path.exists():
                continue
            try:
                content = file_path.read_text(errors="ignore")
            except Exception:
                continue
            headings = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", content, flags=re.IGNORECASE)
            cleaned = []
            for h in headings:
                text = re.sub(r"<[^>]+>", " ", h)
                text = re.sub(r"\s+", " ", text).strip()
                if text:
                    cleaned.append(text)
            if cleaned:
                headings_blocks.append(
                    f"HTML HEADINGS ({art.file_path}):\n- " + "\n- ".join(cleaned[:30])
                )

        return "\n\n".join(headings_blocks)

    def _build_html_section_snippets(self, session_path: str, artifacts: List[Artifact]) -> str:
        session_root = Path(session_path)
        html_paths = [
            session_root / art.file_path
            for art in artifacts
            if art.file_path and art.file_path.lower().endswith(".html")
        ]
        if not html_paths:
            return ""

        try:
            content = html_paths[0].read_text(errors="ignore")
        except Exception:
            return ""

        targets = [
            "Executive Summary",
            "Environmental",
            "Economic",
            "Social",
            "References",
        ]
        snippets: List[str] = ["HTML SECTION SNIPPETS:"]

        for target in targets:
            pattern = re.compile(
                rf"<h[1-3][^>]*>[^<]*{re.escape(target)}[^<]*</h[1-3]>",
                flags=re.IGNORECASE,
            )
            match = pattern.search(content)
            if not match:
                continue
            start = match.end()
            next_heading = re.search(r"<h[1-3][^>]*>", content[start:], flags=re.IGNORECASE)
            end = start + (next_heading.start() if next_heading else 1200)
            chunk = content[start:end]
            text = re.sub(r"<[^>]+>", " ", chunk)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                snippets.append(f"- {target}: {text[:300]}")

        return "\n".join(snippets) if len(snippets) > 1 else ""

    def _build_artifact_previews(
        self,
        session_path: str,
        artifacts: List[Artifact],
        max_total_chars: int = 30000,
        per_file_chars: int = 5000,
    ) -> str:
        if not artifacts:
            return ""

        session_root = Path(session_path)
        text_exts = {".md", ".txt", ".html", ".json"}

        def _priority(path: str) -> int:
            lower = path.lower()
            if lower.endswith("refined_corpus.md"):
                return 0
            if lower.endswith(".html"):
                return 1
            if lower.endswith(".md"):
                return 2
            if lower.endswith(".json"):
                return 3
            return 9

        snippets: List[str] = ["ARTIFACT PREVIEWS:"]
        used = 0

        for art in sorted(artifacts, key=lambda a: _priority(a.file_path or "")):
            if not art.file_path:
                continue
            file_path = session_root / art.file_path
            if not file_path.exists():
                continue
            if file_path.suffix.lower() not in text_exts:
                continue

            try:
                content = file_path.read_text(errors="ignore")
            except Exception:
                continue

            if file_path.suffix.lower() == ".html":
                content = re.sub(
                    r"<(script|style)[\s\S]*?</\1>",
                    " ",
                    content,
                    flags=re.IGNORECASE,
                )
                content = re.sub(r"<[^>]+>", " ", content)
                content = re.sub(r"\s+", " ", content)

            snippet = content[:per_file_chars].strip()
            if not snippet:
                continue

            block = f"--- {art.file_path} ---\n{snippet}"
            if used + len(block) > max_total_chars:
                break
            snippets.append(block)
            used += len(block)

        if len(snippets) == 1:
            return ""
        return "\n\n".join(snippets)
    
    async def _handle_phase_failure(self, phase: Phase, result: PhaseResult) -> bool:
        """Handle a failed phase. Returns True if should continue."""
        # For now, just log and stop. Could add retry logic here.
        self._log(f"Phase {phase.name} failed. Stopping harness.")
        return False
    
    def _persist_plan(self) -> None:
        """Persist plan to file and optionally database."""
        if self.plan and self.file_persistence:
            self.file_persistence.save_plan(self.plan)
        
        if self.plan and self.db_persistence:
            self.db_persistence.save_plan(self.plan)
    
    def _generate_summary(self) -> Dict[str, Any]:
        """Generate final summary."""
        return {
            "status": self.status.value,
            "harness_id": self.session_manager.harness_id if self.session_manager else None,
            "harness_dir": str(self.session_manager.harness_dir) if self.session_manager else None,
            "plan": {
                "name": self.plan.name if self.plan else None,
                "total_phases": len(self.plan.phases) if self.plan else 0,
                "total_tasks": self.plan.total_tasks() if self.plan else 0,
            },
            "phases_completed": sum(1 for r in self.phase_results if r.success),
            "phases_failed": sum(1 for r in self.phase_results if not r.success),
            "phase_results": [
                {
                    "name": r.phase_name,
                    "success": r.success,
                    "session": r.session_path,
                    "artifacts": len(r.artifacts_produced),
                }
                for r in self.phase_results
            ],
        }
    
    @classmethod
    def resume(
        cls,
        harness_dir: Path,
        config: Optional[HarnessConfig] = None,
    ) -> "HarnessOrchestrator":
        """
        Resume a harness from saved state.
        
        Args:
            harness_dir: Path to existing harness directory
            config: Optional config override
            
        Returns:
            HarnessOrchestrator ready to continue
        """
        orchestrator = cls(
            workspaces_root=harness_dir.parent,
            config=config,
        )
        
        # Restore session manager
        orchestrator.session_manager = HarnessSessionManager.resume(harness_dir)
        
        # Restore plan
        plan_file = harness_dir / f"plan_{harness_dir.name.replace('harness_', '')}.json"
        # Fallback to any plan file
        plan_files = list(harness_dir.glob("plan_*.json"))
        if plan_files:
            orchestrator.file_persistence = PlanPersistence(harness_dir)
            orchestrator.plan = orchestrator.file_persistence.load_plan(plan_files[0])
        
        return orchestrator


# Convenience function for entry point
async def run_harness(
    massive_request: str,
    workspaces_root: Path,
    process_turn: ProcessTurnInterface,
    client: Any,
    agent: Any = None,
    config: Optional[HarnessConfig] = None,
    skip_interview: bool = False,
    plan_file: Optional[Path] = None,
    template_file: Optional[Path] = None,
    max_iterations: int = 20,
    harness_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the complete harness flow.
    """
    if config is None:
        config = HarnessConfig(max_iterations=max_iterations)
        use_gateway_env = os.getenv("UA_HARNESS_USE_GATEWAY", "1").lower() in {
            "1",
            "true",
            "yes",
        }
        config.use_gateway = use_gateway_env
    else:
        # Override if passed explicitly
        config.max_iterations = max_iterations

    orchestrator = HarnessOrchestrator(workspaces_root, config)
    return await orchestrator.run(
        massive_request, 
        process_turn, 
        client,
        agent=agent,
        skip_interview=skip_interview,
        plan_file=plan_file,
        template_file=template_file,
        harness_id=harness_id,
    )
