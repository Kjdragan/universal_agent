"""
Universal Ralph Wrapper - Orchestrator

The outer loop that orchestrates long-running task execution.
This is the programmatic harness (NOT an agent) that:
1. Manages the task queue
2. Spawns fresh agent instances for each iteration
3. Evaluates completion
4. Handles checkpointing and recovery
5. Triggers re-planning when needed

This is analogous to Ralph's bash loop but designed for universal tasks.
"""

import asyncio
import time
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Protocol
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from urw_state import (
    URWStateManager, Task, TaskStatus, CompletionConfidence,
    Artifact, ArtifactType, IterationResult
)
from urw_decomposer import PlanManager, HybridDecomposer, Decomposer
from urw_evaluator import CompositeEvaluator, EvaluationResult


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class URWConfig:
    """Configuration for the Universal Ralph Wrapper orchestrator."""
    
    # Iteration limits
    max_iterations_per_task: int = 15
    max_total_iterations: int = 200
    max_consecutive_failures: int = 3
    
    # Completion thresholds
    min_completion_confidence: CompletionConfidence = CompletionConfidence.MEDIUM
    
    # Re-planning
    enable_dynamic_replanning: bool = True
    require_human_approval_for_replan: bool = False
    
    # Checkpointing
    checkpoint_every_n_iterations: int = 1  # Checkpoint every iteration by default
    
    # Timeouts (seconds)
    task_timeout: Optional[int] = 3600  # 1 hour per task
    iteration_timeout: Optional[int] = 600  # 10 minutes per iteration
    
    # Behavior
    pause_on_blockers: bool = True
    auto_decompose_failed_tasks: bool = True
    
    # Logging
    verbose: bool = True


class OrchestratorStatus(Enum):
    """Status of the orchestrator."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETE = "complete"
    FAILED = "failed"


# =============================================================================
# AGENT LOOP INTERFACE
# =============================================================================

class AgentLoopInterface(Protocol):
    """
    Protocol for the agent loop that executes tasks.
    
    Your existing multi-agent system should implement this interface.
    The orchestrator calls this for each task, expecting:
    - Fresh context window each call
    - Task execution until completion or failure
    - Structured result returned
    """
    
    async def execute_task(self, 
                          task: Task, 
                          context: str,
                          workspace_path: Path) -> 'AgentExecutionResult':
        """
        Execute a single task with the provided context.
        
        IMPORTANT: Each call should use a fresh agent instance with
        a clean context window. The only context the agent receives
        is what's passed in the `context` parameter.
        
        Args:
            task: The task to execute
            context: Pre-built context string from URW state manager
            workspace_path: Path to workspace for file operations
        
        Returns:
            AgentExecutionResult with execution details
        """
        ...
    
    async def cancel(self):
        """Cancel any running execution."""
        ...


@dataclass
class AgentExecutionResult:
    """Result from agent task execution."""
    
    # Execution outcome
    success: bool
    output: str
    error: Optional[str] = None
    
    # Artifacts produced (file paths relative to artifacts dir)
    artifacts_produced: List[Dict] = field(default_factory=list)
    # Format: [{"path": "report.md", "type": "file", "metadata": {...}}, ...]
    
    # Side effects executed
    side_effects: List[Dict] = field(default_factory=list)
    # Format: [{"type": "email_sent", "key": "...", "details": {...}}, ...]
    
    # Learnings extracted
    learnings: List[str] = field(default_factory=list)
    
    # Failed approaches discovered
    failed_approaches: List[Dict] = field(default_factory=list)
    # Format: [{"approach": "...", "why_failed": "..."}, ...]
    
    # Metrics
    context_tokens_used: int = 0
    tools_invoked: List[str] = field(default_factory=list)
    execution_time_seconds: float = 0


# =============================================================================
# CALLBACKS
# =============================================================================

@dataclass
class URWCallbacks:
    """Callbacks for orchestrator events."""
    
    # Task lifecycle
    on_task_start: Optional[Callable[[Task, int], None]] = None
    on_task_complete: Optional[Callable[[Task, EvaluationResult], None]] = None
    on_task_failed: Optional[Callable[[Task, str], None]] = None
    
    # Iteration lifecycle
    on_iteration_start: Optional[Callable[[int, Task], None]] = None
    on_iteration_end: Optional[Callable[[int, IterationResult], None]] = None
    
    # Plan lifecycle
    on_plan_created: Optional[Callable[[List[Task]], None]] = None
    on_plan_revised: Optional[Callable[[str, List[Task]], None]] = None
    on_plan_complete: Optional[Callable[[Dict], None]] = None
    
    # Human intervention
    on_human_review_required: Optional[Callable[[Task, EvaluationResult], bool]] = None
    on_replan_approval_required: Optional[Callable[[str], bool]] = None
    
    # Errors and blockers
    on_blocker_detected: Optional[Callable[[Task, str], None]] = None
    on_error: Optional[Callable[[Exception, Optional[Task]], None]] = None
    
    # Progress
    on_progress: Optional[Callable[[str], None]] = None


# =============================================================================
# ORCHESTRATOR
# =============================================================================

class URWOrchestrator:
    """
    The Universal Ralph Wrapper orchestrator.
    
    This is the outer loop that manages long-running task execution.
    It is NOT an agent - it's a programmatic harness that:
    - Schedules tasks based on dependencies
    - Spawns fresh agent instances for each iteration
    - Evaluates completion using multiple strategies
    - Handles failures and re-planning
    - Maintains persistent state across context resets
    
    Usage:
        orchestrator = URWOrchestrator(
            agent_loop=your_multi_agent_system,
            llm_client=anthropic_client,
            workspace_path=Path("./workspace")
        )
        
        result = await orchestrator.run("Research quantum computing and write a report")
    """
    
    def __init__(self,
                 agent_loop: AgentLoopInterface,
                 llm_client: Any,
                 workspace_path: Path,
                 config: Optional[URWConfig] = None,
                 callbacks: Optional[URWCallbacks] = None,
                 decomposer: Optional[Decomposer] = None):
        """
        Initialize the orchestrator.
        
        Args:
            agent_loop: Your multi-agent system implementing AgentLoopInterface
            llm_client: Anthropic client for decomposition and evaluation
            workspace_path: Path to workspace directory
            config: Orchestrator configuration
            callbacks: Event callbacks
            decomposer: Custom decomposer (defaults to HybridDecomposer)
        """
        self.agent_loop = agent_loop
        self.llm_client = llm_client
        self.workspace_path = Path(workspace_path)
        self.config = config or URWConfig()
        self.callbacks = callbacks or URWCallbacks()
        
        # Initialize state manager
        self.state_manager = URWStateManager(self.workspace_path)
        
        # Initialize decomposer and plan manager
        self.decomposer = decomposer or HybridDecomposer(llm_client)
        self.plan_manager = PlanManager(self.state_manager, self.decomposer)
        
        # Initialize evaluator
        self.evaluator = CompositeEvaluator(
            llm_client=llm_client,
            state_manager=self.state_manager
        )
        
        # Runtime state
        self.status = OrchestratorStatus.IDLE
        self.current_task: Optional[Task] = None
        self.current_iteration: int = 0
        self.total_iterations: int = 0
        self._should_stop = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused initially
    
    # =========================================================================
    # MAIN ENTRY POINTS
    # =========================================================================
    
    async def run(self, request: str, 
                  context: Optional[Dict] = None) -> Dict:
        """
        Execute a complete user request.
        
        This is the main entry point. It:
        1. Decomposes the request into tasks
        2. Executes tasks in dependency order
        3. Handles failures and re-planning
        4. Returns final status
        
        Args:
            request: User's original request
            context: Optional initial context
        
        Returns:
            Dict with execution summary
        """
        self._log(f"Starting URW for request: {request[:100]}...")
        self.status = OrchestratorStatus.RUNNING
        self._should_stop = False
        
        try:
            # Create plan
            tasks = self.plan_manager.create_plan(request, context)
            self._log(f"Created plan with {len(tasks)} tasks")
            
            if self.callbacks.on_plan_created:
                self.callbacks.on_plan_created(tasks)
            
            # Run main loop
            result = await self._main_loop()
            
            return result
            
        except Exception as e:
            self.status = OrchestratorStatus.FAILED
            self._log(f"Orchestrator failed: {e}")
            if self.callbacks.on_error:
                self.callbacks.on_error(e, self.current_task)
            raise
        finally:
            self.state_manager.close()
    
    async def resume(self, checkpoint_sha: Optional[str] = None) -> Dict:
        """
        Resume execution from a checkpoint.
        
        Args:
            checkpoint_sha: Git commit SHA to resume from (latest if None)
        
        Returns:
            Dict with execution summary
        """
        self._log(f"Resuming URW execution...")
        self.status = OrchestratorStatus.RUNNING
        self._should_stop = False
        
        if checkpoint_sha:
            self.state_manager.checkpointer.rollback_to(checkpoint_sha)
        
        try:
            result = await self._main_loop()
            return result
        except Exception as e:
            self.status = OrchestratorStatus.FAILED
            if self.callbacks.on_error:
                self.callbacks.on_error(e, self.current_task)
            raise
        finally:
            self.state_manager.close()
    
    def pause(self):
        """Pause execution after current iteration."""
        self._pause_event.clear()
        self.status = OrchestratorStatus.PAUSED
        self._log("Orchestrator paused")
    
    def resume_pause(self):
        """Resume from paused state."""
        self._pause_event.set()
        self.status = OrchestratorStatus.RUNNING
        self._log("Orchestrator resumed")
    
    def stop(self):
        """Stop execution gracefully."""
        self._should_stop = True
        self._pause_event.set()  # Unpause if paused
        self._log("Orchestrator stopping...")
    
    # =========================================================================
    # MAIN LOOP
    # =========================================================================
    
    async def _main_loop(self) -> Dict:
        """
        The main execution loop.
        
        This is analogous to Ralph's bash while loop:
        - Get next task
        - Execute with fresh agent
        - Evaluate completion
        - Handle result
        - Repeat until done
        """
        consecutive_failures = 0
        
        while not self._should_exit():
            # Wait if paused
            await self._pause_event.wait()
            
            # Get next executable task
            task = self.state_manager.get_next_task()
            
            if task is None:
                # No more tasks - check if we're done or blocked
                if self.state_manager.is_plan_complete():
                    self._log("Plan complete!")
                    self.status = OrchestratorStatus.COMPLETE
                    break
                else:
                    # Tasks exist but none are executable (blocked)
                    blocked_tasks = self.state_manager.get_tasks_by_status(TaskStatus.BLOCKED)
                    if blocked_tasks and self.config.pause_on_blockers:
                        self._log(f"Blocked: {len(blocked_tasks)} tasks blocked")
                        if self.callbacks.on_blocker_detected:
                            self.callbacks.on_blocker_detected(
                                blocked_tasks[0], 
                                "Dependencies not satisfied"
                            )
                        self.status = OrchestratorStatus.PAUSED
                        await self._pause_event.wait()
                        continue
                    else:
                        # No executable tasks and not pausing - something's wrong
                        self._log("No executable tasks but plan not complete - possible deadlock")
                        self.status = OrchestratorStatus.FAILED
                        break
            
            self.current_task = task
            
            # Check task iteration limit
            task_iterations = self.state_manager.get_task_iteration_count(task.id)
            if task_iterations >= task.max_iterations:
                self._log(f"Task '{task.title}' exceeded max iterations ({task.max_iterations})")
                await self._handle_task_failure(task, "Exceeded maximum iterations")
                consecutive_failures += 1
                
                if consecutive_failures >= self.config.max_consecutive_failures:
                    if self.config.enable_dynamic_replanning:
                        await self._trigger_replan(f"Too many consecutive failures on task '{task.title}'")
                        consecutive_failures = 0
                    else:
                        self._log("Too many consecutive failures - stopping")
                        self.status = OrchestratorStatus.FAILED
                        break
                continue
            
            # Execute iteration
            try:
                result = await self._execute_iteration(task)
                
                if result.outcome in ['success', 'partial']:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                
            except asyncio.TimeoutError:
                self._log(f"Iteration timeout on task '{task.title}'")
                consecutive_failures += 1
                
            except Exception as e:
                self._log(f"Iteration error: {e}")
                traceback.print_exc()
                consecutive_failures += 1
                if self.callbacks.on_error:
                    self.callbacks.on_error(e, task)
            
            # Check consecutive failure limit
            if consecutive_failures >= self.config.max_consecutive_failures:
                if self.config.enable_dynamic_replanning:
                    await self._trigger_replan(f"Consecutive failures: {consecutive_failures}")
                    consecutive_failures = 0
                else:
                    self._log("Too many consecutive failures - stopping")
                    self.status = OrchestratorStatus.FAILED
                    break
            
            # Check total iteration limit
            if self.total_iterations >= self.config.max_total_iterations:
                self._log(f"Reached maximum total iterations ({self.config.max_total_iterations})")
                self.status = OrchestratorStatus.PAUSED
                break
        
        # Generate final summary
        return self._generate_summary()
    
    async def _execute_iteration(self, task: Task) -> IterationResult:
        """Execute a single iteration for a task."""
        
        self.total_iterations += 1
        iteration = self.state_manager.start_iteration(task.id)
        self.current_iteration = iteration
        
        self._log(f"[Iteration {iteration}] Starting task: {task.title}")
        
        if self.callbacks.on_iteration_start:
            self.callbacks.on_iteration_start(iteration, task)
        
        # Mark task as in progress
        self.state_manager.update_task_status(
            task.id, TaskStatus.IN_PROGRESS, iteration
        )
        
        if self.callbacks.on_task_start:
            self.callbacks.on_task_start(task, iteration)
        
        # Generate context for the agent
        context = self.state_manager.generate_agent_context(task)
        
        # Execute via the agent loop (your multi-agent system)
        start_time = time.time()
        
        try:
            if self.config.iteration_timeout:
                agent_result = await asyncio.wait_for(
                    self.agent_loop.execute_task(task, context, self.workspace_path),
                    timeout=self.config.iteration_timeout
                )
            else:
                agent_result = await self.agent_loop.execute_task(
                    task, context, self.workspace_path
                )
        except asyncio.TimeoutError:
            agent_result = AgentExecutionResult(
                success=False,
                output="Iteration timed out",
                error="Timeout",
                execution_time_seconds=time.time() - start_time
            )
        
        execution_time = time.time() - start_time
        
        # Register artifacts
        artifact_ids = []
        for art_data in agent_result.artifacts_produced:
            artifact = Artifact(
                id=f"art_{iteration}_{len(artifact_ids)}",
                task_id=task.id,
                artifact_type=ArtifactType(art_data.get('type', 'file')),
                file_path=art_data.get('path'),
                metadata=art_data.get('metadata')
            )
            self.state_manager.register_artifact(artifact)
            artifact_ids.append(artifact.id)
        
        # Record side effects
        for effect in agent_result.side_effects:
            self.state_manager.record_side_effect(
                task_id=task.id,
                effect_type=effect['type'],
                idempotency_key=effect['key'],
                details=effect.get('details', {}),
                iteration=iteration
            )
        
        # Get artifacts for evaluation
        artifacts = self.state_manager.get_task_artifacts(task.id)
        
        # Evaluate completion
        evaluation = self.evaluator.evaluate(
            task, artifacts, agent_result.output, self.workspace_path
        )
        
        # Determine outcome
        if evaluation.is_complete and evaluation.confidence.value in ['definitive', 'high', 'medium']:
            outcome = 'success'
        elif agent_result.success and evaluation.overall_score >= 0.4:
            outcome = 'partial'
        elif agent_result.error:
            outcome = 'failed'
        else:
            outcome = 'incomplete'
        
        # Build iteration result
        result = IterationResult(
            iteration=iteration,
            task_id=task.id,
            outcome=outcome,
            completion_confidence=evaluation.confidence,
            context_tokens_used=agent_result.context_tokens_used,
            tools_invoked=agent_result.tools_invoked,
            learnings=agent_result.learnings,
            artifacts_produced=artifact_ids,
            failed_approaches=agent_result.failed_approaches,
            agent_output=agent_result.output[:10000]  # Truncate for storage
        )
        
        # Complete iteration (creates checkpoint)
        commit_sha = self.state_manager.complete_iteration(result)
        result.commit_sha = commit_sha
        
        self._log(f"[Iteration {iteration}] Outcome: {outcome} (score: {evaluation.overall_score:.2f})")
        
        if self.callbacks.on_iteration_end:
            self.callbacks.on_iteration_end(iteration, result)
        
        # Handle task status based on evaluation
        if evaluation.is_complete:
            await self._handle_task_complete(task, evaluation)
        elif outcome == 'failed':
            # Don't immediately fail - might retry
            pass
        
        return result
    
    async def _handle_task_complete(self, task: Task, evaluation: EvaluationResult):
        """Handle successful task completion."""
        
        # Check if human review required
        if task.verification_type == 'human':
            if self.callbacks.on_human_review_required:
                approved = self.callbacks.on_human_review_required(task, evaluation)
                if not approved:
                    self._log(f"Task '{task.title}' pending human approval")
                    self.state_manager.update_task_status(
                        task.id, TaskStatus.NEEDS_REVIEW
                    )
                    return
        
        # Mark complete
        self.state_manager.update_task_status(
            task.id, TaskStatus.COMPLETE, self.current_iteration
        )
        
        self._log(f"Task '{task.title}' completed!")
        
        if self.callbacks.on_task_complete:
            self.callbacks.on_task_complete(task, evaluation)
    
    async def _handle_task_failure(self, task: Task, reason: str):
        """Handle task failure."""
        
        self._log(f"Task '{task.title}' failed: {reason}")
        
        if self.config.auto_decompose_failed_tasks:
            # Try to decompose into smaller tasks
            self._log(f"Attempting to decompose failed task...")
            try:
                sub_tasks = self.plan_manager.decompose_failed_task(task, reason)
                self._log(f"Decomposed into {len(sub_tasks)} sub-tasks")
                return
            except Exception as e:
                self._log(f"Decomposition failed: {e}")
        
        # Mark as failed
        self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
        
        if self.callbacks.on_task_failed:
            self.callbacks.on_task_failed(task, reason)
    
    async def _trigger_replan(self, reason: str):
        """Trigger plan revision."""
        
        self._log(f"Triggering replan: {reason}")
        
        if self.config.require_human_approval_for_replan:
            if self.callbacks.on_replan_approval_required:
                approved = self.callbacks.on_replan_approval_required(reason)
                if not approved:
                    self._log("Replan not approved - pausing")
                    self.status = OrchestratorStatus.WAITING_FOR_HUMAN
                    return
        
        # Get context for replanning
        context = {
            'learnings': [],
            'reason': reason
        }
        
        # Get recent learnings
        recent = self.state_manager.get_recent_iterations(10)
        for r in recent:
            if r.get('learnings'):
                import json
                learnings = json.loads(r['learnings'])
                context['learnings'].extend(learnings)
        
        # Revise plan
        new_tasks = self.plan_manager.revise_plan(reason, context=context)
        
        self._log(f"Plan revised with {len(new_tasks)} new tasks")
        
        if self.callbacks.on_plan_revised:
            self.callbacks.on_plan_revised(reason, new_tasks)
    
    def _should_exit(self) -> bool:
        """Check if we should exit the main loop."""
        return (
            self._should_stop or 
            self.status in [OrchestratorStatus.COMPLETE, OrchestratorStatus.FAILED]
        )
    
    def _generate_summary(self) -> Dict:
        """Generate execution summary."""
        
        stats = self.state_manager.get_completion_stats()
        all_tasks = self.state_manager.get_all_tasks()
        
        return {
            "status": self.status.value,
            "original_request": self.state_manager.get_original_request(),
            "total_iterations": self.total_iterations,
            "task_stats": stats,
            "tasks_completed": stats.get('complete', 0),
            "tasks_failed": stats.get('failed', 0),
            "tasks_pending": stats.get('pending', 0),
            "is_complete": self.state_manager.is_plan_complete(),
            "workspace_path": str(self.workspace_path),
            "artifacts_dir": str(self.workspace_path / '.urw' / 'artifacts'),
            "final_checkpoint": self.state_manager.checkpointer.get_current_sha(),
        }
    
    def _log(self, message: str):
        """Log a message."""
        if self.config.verbose:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[URW {timestamp}] {message}")
        
        if self.callbacks.on_progress:
            self.callbacks.on_progress(message)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def run_universal_task(
    request: str,
    agent_loop: AgentLoopInterface,
    llm_client: Any,
    workspace_path: Optional[Path] = None,
    config: Optional[URWConfig] = None,
) -> Dict:
    """
    Convenience function to run a universal task.
    
    Args:
        request: User's request
        agent_loop: Your multi-agent system
        llm_client: Anthropic client
        workspace_path: Workspace directory (default: ./urw_workspace)
        config: Optional configuration
    
    Returns:
        Execution summary dict
    """
    workspace = workspace_path or Path("./urw_workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    
    orchestrator = URWOrchestrator(
        agent_loop=agent_loop,
        llm_client=llm_client,
        workspace_path=workspace,
        config=config,
    )
    
    return await orchestrator.run(request)


def create_orchestrator_with_callbacks(
    agent_loop: AgentLoopInterface,
    llm_client: Any,
    workspace_path: Path,
    on_progress: Optional[Callable[[str], None]] = None,
    on_task_complete: Optional[Callable[[Task, EvaluationResult], None]] = None,
    on_plan_complete: Optional[Callable[[Dict], None]] = None,
    config: Optional[URWConfig] = None,
) -> URWOrchestrator:
    """
    Create an orchestrator with common callbacks pre-configured.
    
    Args:
        agent_loop: Your multi-agent system
        llm_client: Anthropic client
        workspace_path: Workspace directory
        on_progress: Progress callback
        on_task_complete: Task completion callback
        on_plan_complete: Plan completion callback
        config: Optional configuration
    
    Returns:
        Configured URWOrchestrator
    """
    callbacks = URWCallbacks(
        on_progress=on_progress,
        on_task_complete=on_task_complete,
        on_plan_complete=on_plan_complete,
    )
    
    return URWOrchestrator(
        agent_loop=agent_loop,
        llm_client=llm_client,
        workspace_path=workspace_path,
        config=config,
        callbacks=callbacks,
    )
