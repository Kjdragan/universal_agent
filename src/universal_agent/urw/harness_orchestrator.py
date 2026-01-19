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


@dataclass
class PhaseResult:
    """Result of a phase execution."""
    phase_id: str
    phase_name: str
    success: bool
    session_path: str
    artifacts_produced: List[str] = field(default_factory=list)
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
       c. Feed to multi-agent system (process_turn)
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
        skip_interview: bool = False,
        plan_file: Optional[Path] = None,
        template_file: Optional[Path] = None,
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
        self.session_manager = HarnessSessionManager(self.workspaces_root)
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
        
        for phase in self.plan.phases:
            result = await self._execute_phase(phase, process_turn, client)
            self.phase_results.append(result)
            
            if result.success:
                self.plan.mark_phase_complete(phase.id)
                self._persist_plan()
            else:
                self._log(f"Phase {phase.name} failed: {result.error}")
                if not await self._handle_phase_failure(phase, result):
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
    ) -> PhaseResult:
        """Execute a single phase."""
        self._log(f"=== Phase {phase.order + 1}: {phase.name} ===")
        
        # 1. Toggle to new session directory
        session_path = self.session_manager.next_phase_session()
        phase.session_path = session_path
        self._log(f"Session: {session_path}")
        
        # 2. Check compaction strategy
        compact_result = compact_agent_context(client, self.config.force_new_client_between_phases)
        self._log(f"Context: {compact_result['notes']}")
        
        # 3. Build phase prompt
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
        
        # 4. Execute via multi-agent system
        try:
            result = await process_turn(
                client,
                phase_prompt,
                session_path,
            )
            
            # 5. Evaluate (simplified - just check for artifacts in session)
            artifacts = self._scan_session_artifacts(session_path)
            success = len(artifacts) > 0 or result is not None
            
            return PhaseResult(
                phase_id=str(phase.id),
                phase_name=phase.name,
                success=success,
                session_path=session_path,
                artifacts_produced=artifacts,
            )
            
        except Exception as e:
            self._log(f"Phase execution error: {e}")
            return PhaseResult(
                phase_id=str(phase.id),
                phase_name=phase.name,
                success=False,
                session_path=session_path,
                error=str(e),
            )
    
    def _scan_session_artifacts(self, session_path: str) -> List[str]:
        """Scan session directory for produced artifacts."""
        artifacts = []
        work_products = Path(session_path) / "work_products"
        
        if work_products.exists():
            for f in work_products.rglob("*"):
                if f.is_file():
                    artifacts.append(str(f.relative_to(session_path)))
        
        return artifacts
    
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
    config: Optional[HarnessConfig] = None,
    skip_interview: bool = False,
    plan_file: Optional[Path] = None,
    template_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Run the complete harness flow.
    
    Args:
        massive_request: The user's massive request
        workspaces_root: Root directory for workspaces
        process_turn: The main agent's process_turn function
        client: The ClaudeSDKClient
        config: Optional config
        skip_interview: If True, skip interview and use plan_file
        plan_file: Path to JSON plan file (used when skip_interview=True)
        template_file: Path to interview transcript JSON (for template-based planning)
        
    Returns:
        Summary dict
    """
    orchestrator = HarnessOrchestrator(workspaces_root, config)
    return await orchestrator.run(
        massive_request, 
        process_turn, 
        client,
        skip_interview=skip_interview,
        plan_file=plan_file,
        template_file=template_file,
    )
