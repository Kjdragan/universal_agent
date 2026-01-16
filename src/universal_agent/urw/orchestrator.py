"""
Universal Ralph Wrapper - Orchestrator

Outer loop that orchestrates long-running task execution.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .state import (
    Artifact,
    ArtifactType,
    CompletionConfidence,
    IterationResult,
    Task,
    TaskStatus,
    URWStateManager,
)
from .decomposer import Decomposer, HybridDecomposer, PlanManager
from .evaluator import CompositeEvaluator, EvaluationResult


@dataclass
class URWConfig:
    """Configuration for URW orchestrator."""

    max_iterations_per_task: int = 15
    max_total_iterations: int = 200
    max_consecutive_failures: int = 3

    min_completion_confidence: CompletionConfidence = CompletionConfidence.MEDIUM

    enable_dynamic_replanning: bool = True
    require_human_approval_for_replan: bool = False

    checkpoint_every_n_iterations: int = 1

    task_timeout: Optional[int] = 3600
    iteration_timeout: Optional[int] = 600

    pause_on_blockers: bool = True
    auto_decompose_failed_tasks: bool = True

    llm_model: str = field(
        default_factory=lambda: os.getenv(
            "ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-20250514"
        )
    )

    verbose: bool = True


class OrchestratorStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETE = "complete"
    FAILED = "failed"


class AgentLoopInterface(Protocol):
    async def execute_task(
        self, task: Task, context: str, workspace_path: Path
    ) -> "AgentExecutionResult":
        ...

    async def cancel(self):
        ...


@dataclass
class AgentExecutionResult:
    success: bool
    output: str
    error: Optional[str] = None

    artifacts_produced: List[Dict[str, Any]] = field(default_factory=list)
    side_effects: List[Dict[str, Any]] = field(default_factory=list)
    learnings: List[str] = field(default_factory=list)
    failed_approaches: List[Dict[str, Any]] = field(default_factory=list)

    context_tokens_used: int = 0
    tools_invoked: List[str] = field(default_factory=list)
    execution_time_seconds: float = 0


@dataclass
class URWCallbacks:
    on_task_start: Optional[Callable[[Task, int], None]] = None
    on_task_complete: Optional[Callable[[Task, EvaluationResult], None]] = None
    on_task_failed: Optional[Callable[[Task, str], None]] = None

    on_iteration_start: Optional[Callable[[int, Task], None]] = None
    on_iteration_end: Optional[Callable[[int, IterationResult], None]] = None

    on_plan_created: Optional[Callable[[List[Task]], None]] = None
    on_plan_revised: Optional[Callable[[str, List[Task]], None]] = None
    on_plan_complete: Optional[Callable[[Dict[str, Any]], None]] = None

    on_human_review_required: Optional[Callable[[Task, EvaluationResult], bool]] = None
    on_replan_approval_required: Optional[Callable[[str], bool]] = None

    on_blocker_detected: Optional[Callable[[Task, str], None]] = None
    on_error: Optional[Callable[[Exception, Optional[Task]], None]] = None

    on_progress: Optional[Callable[[str], None]] = None


class URWOrchestrator:
    def __init__(
        self,
        agent_loop: AgentLoopInterface,
        llm_client: Any,
        workspace_path: Path,
        config: Optional[URWConfig] = None,
        callbacks: Optional[URWCallbacks] = None,
        decomposer: Optional[Decomposer] = None,
    ):
        self.agent_loop = agent_loop
        self.llm_client = llm_client
        self.workspace_path = Path(workspace_path)
        self.config = config or URWConfig()
        self.callbacks = callbacks or URWCallbacks()

        self.state_manager = URWStateManager(self.workspace_path)
        self.decomposer = decomposer or HybridDecomposer(
            llm_client,
            model=self.config.llm_model,
        )
        self.plan_manager = PlanManager(self.state_manager, self.decomposer)
        self.evaluator = CompositeEvaluator(
            llm_client=llm_client,
            state_manager=self.state_manager,
            model=self.config.llm_model,
        )

        self.status = OrchestratorStatus.IDLE
        self.current_task: Optional[Task] = None
        self.current_iteration: int = 0
        self.total_iterations: int = 0
        self._should_stop = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()

    async def run(self, request: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._log(f"Starting URW for request: {request[:120]}...")
        self.status = OrchestratorStatus.RUNNING
        self._should_stop = False

        try:
            tasks = self.plan_manager.create_plan(request, context)
            self._log(f"Created plan with {len(tasks)} tasks")
            if self.callbacks.on_plan_created:
                self.callbacks.on_plan_created(tasks)
            result = await self._main_loop()
            return result
        except Exception as exc:
            self.status = OrchestratorStatus.FAILED
            self._log(f"Orchestrator failed: {exc}")
            if self.callbacks.on_error:
                self.callbacks.on_error(exc, self.current_task)
            raise
        finally:
            self.state_manager.close()

    async def resume(self, checkpoint_sha: Optional[str] = None) -> Dict[str, Any]:
        self._log("Resuming URW execution...")
        self.status = OrchestratorStatus.RUNNING
        self._should_stop = False

        if checkpoint_sha:
            self.state_manager.checkpointer.rollback_to(checkpoint_sha)

        try:
            result = await self._main_loop()
            return result
        except Exception as exc:
            self.status = OrchestratorStatus.FAILED
            if self.callbacks.on_error:
                self.callbacks.on_error(exc, self.current_task)
            raise
        finally:
            self.state_manager.close()

    def pause(self) -> None:
        self._pause_event.clear()
        self.status = OrchestratorStatus.PAUSED
        self._log("Orchestrator paused")

    def resume_pause(self) -> None:
        self._pause_event.set()
        self.status = OrchestratorStatus.RUNNING
        self._log("Orchestrator resumed")

    def stop(self) -> None:
        self._should_stop = True
        self._pause_event.set()
        self._log("Orchestrator stopping...")

    async def _main_loop(self) -> Dict[str, Any]:
        consecutive_failures = 0

        while not self._should_exit():
            await self._pause_event.wait()

            task = self.state_manager.get_next_task()
            if task is None:
                if self.state_manager.is_plan_complete():
                    self._log("Plan complete!")
                    self.status = OrchestratorStatus.COMPLETE
                    break

                blocked_tasks = self.state_manager.get_tasks_by_status(TaskStatus.BLOCKED)
                if blocked_tasks and self.config.pause_on_blockers:
                    self._log(f"Blocked: {len(blocked_tasks)} tasks blocked")
                    if self.callbacks.on_blocker_detected:
                        self.callbacks.on_blocker_detected(
                            blocked_tasks[0], "Dependencies not satisfied"
                        )
                    self.status = OrchestratorStatus.PAUSED
                    await self._pause_event.wait()
                    continue

                self._log("No executable tasks but plan not complete - possible deadlock")
                self.status = OrchestratorStatus.FAILED
                break

            self.current_task = task

            task_iterations = self.state_manager.get_task_iteration_count(task.id)
            if task_iterations >= task.max_iterations:
                await self._handle_task_failure(task, "Exceeded maximum iterations")
                consecutive_failures += 1
                if consecutive_failures >= self.config.max_consecutive_failures:
                    if self.config.enable_dynamic_replanning:
                        await self._trigger_replan(
                            f"Too many consecutive failures on task '{task.title}'"
                        )
                        consecutive_failures = 0
                    else:
                        self._log("Too many consecutive failures - stopping")
                        self.status = OrchestratorStatus.FAILED
                        break
                continue

            try:
                result = await self._execute_iteration(task)
                consecutive_failures = 0 if result.outcome in {"success", "partial"} else consecutive_failures + 1
            except asyncio.TimeoutError:
                self._log(f"Iteration timeout on task '{task.title}'")
                consecutive_failures += 1
            except Exception as exc:
                self._log(f"Iteration error: {exc}")
                traceback.print_exc()
                consecutive_failures += 1
                if self.callbacks.on_error:
                    self.callbacks.on_error(exc, task)

            if consecutive_failures >= self.config.max_consecutive_failures:
                if self.config.enable_dynamic_replanning:
                    await self._trigger_replan(f"Consecutive failures: {consecutive_failures}")
                    consecutive_failures = 0
                else:
                    self._log("Too many consecutive failures - stopping")
                    self.status = OrchestratorStatus.FAILED
                    break

            if self.total_iterations >= self.config.max_total_iterations:
                self._log(
                    f"Reached maximum total iterations ({self.config.max_total_iterations})"
                )
                self.status = OrchestratorStatus.PAUSED
                break

        return self._generate_summary()

    async def _execute_iteration(self, task: Task) -> IterationResult:
        self.total_iterations += 1
        iteration = self.state_manager.start_iteration(task.id)
        self.current_iteration = iteration

        self._log(f"[Iteration {iteration}] Starting task: {task.title}")
        if self.callbacks.on_iteration_start:
            self.callbacks.on_iteration_start(iteration, task)

        self.state_manager.update_task_status(task.id, TaskStatus.IN_PROGRESS, iteration)
        if self.callbacks.on_task_start:
            self.callbacks.on_task_start(task, iteration)

        context = self.state_manager.generate_agent_context(task)

        start_time = time.time()
        try:
            if self.config.iteration_timeout:
                agent_result = await asyncio.wait_for(
                    self.agent_loop.execute_task(task, context, self.workspace_path),
                    timeout=self.config.iteration_timeout,
                )
            else:
                agent_result = await self.agent_loop.execute_task(task, context, self.workspace_path)
        except asyncio.TimeoutError:
            agent_result = AgentExecutionResult(
                success=False,
                output="Iteration timed out",
                error="Timeout",
                execution_time_seconds=time.time() - start_time,
            )

        execution_time = time.time() - start_time
        agent_result.execution_time_seconds = execution_time

        artifact_ids: List[str] = []
        for art_data in agent_result.artifacts_produced:
            art_type = art_data.get("type", "file")
            try:
                artifact_type = ArtifactType(art_type)
            except ValueError:
                artifact_type = ArtifactType.FILE
            artifact = Artifact(
                id=f"art_{iteration}_{len(artifact_ids)}",
                task_id=task.id,
                artifact_type=artifact_type,
                file_path=art_data.get("path"),
                metadata=art_data.get("metadata"),
            )
            self.state_manager.register_artifact(artifact)
            artifact_ids.append(artifact.id)

        for effect in agent_result.side_effects:
            self.state_manager.record_side_effect(
                task_id=task.id,
                effect_type=effect["type"],
                idempotency_key=effect["key"],
                details=effect.get("details", {}),
                iteration=iteration,
            )

        artifacts = self.state_manager.get_task_artifacts(task.id)
        evaluation = self.evaluator.evaluate(task, artifacts, agent_result.output, self.workspace_path)

        if evaluation.is_complete and evaluation.confidence.value in {"definitive", "high", "medium"}:
            outcome = "success"
        elif agent_result.success and evaluation.overall_score >= 0.4:
            outcome = "partial"
        elif agent_result.error:
            outcome = "failed"
        else:
            outcome = "incomplete"

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
            agent_output=agent_result.output[:10000],
        )

        commit_sha = self.state_manager.complete_iteration(result)
        result.commit_sha = commit_sha

        evidence_type, evidence_refs = self._summarize_evidence(task, artifacts)
        self.state_manager.write_verification_finding(
            task_id=task.id,
            iteration=iteration,
            status="pass" if evaluation.is_complete else "fail",
            evidence_type=evidence_type,
            evidence_refs=evidence_refs,
            summary={
                "evaluation": evaluation.to_dict(),
                "outcome": outcome,
                "execution_time_seconds": execution_time,
            },
        )

        self._log(f"[Iteration {iteration}] Outcome: {outcome} (score: {evaluation.overall_score:.2f})")
        if self.callbacks.on_iteration_end:
            self.callbacks.on_iteration_end(iteration, result)

        if evaluation.is_complete:
            await self._handle_task_complete(task, evaluation)
        else:
            if outcome == "failed":
                await self._handle_task_failure(task, "Iteration failed")
            else:
                self.state_manager.update_task_status(task.id, TaskStatus.PENDING)

        return result

    async def _handle_task_complete(self, task: Task, evaluation: EvaluationResult) -> None:
        if task.verification_type == "human":
            if self.callbacks.on_human_review_required:
                approved = self.callbacks.on_human_review_required(task, evaluation)
                if not approved:
                    self._log(f"Task '{task.title}' pending human approval")
                    self.state_manager.update_task_status(task.id, TaskStatus.NEEDS_REVIEW)
                    return

        self.state_manager.update_task_status(task.id, TaskStatus.COMPLETE, self.current_iteration)
        self._log(f"Task '{task.title}' completed!")
        if self.callbacks.on_task_complete:
            self.callbacks.on_task_complete(task, evaluation)

    async def _handle_task_failure(self, task: Task, reason: str) -> None:
        self._log(f"Task '{task.title}' failed: {reason}")

        if self.config.auto_decompose_failed_tasks:
            self._log("Attempting to decompose failed task...")
            try:
                sub_tasks = self.plan_manager.decompose_failed_task(task, reason)
                self._log(f"Decomposed into {len(sub_tasks)} sub-tasks")
                return
            except Exception as exc:
                self._log(f"Decomposition failed: {exc}")

        self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
        if self.callbacks.on_task_failed:
            self.callbacks.on_task_failed(task, reason)

    async def _trigger_replan(self, reason: str) -> None:
        self._log(f"Triggering replan: {reason}")
        if self.config.require_human_approval_for_replan:
            if self.callbacks.on_replan_approval_required:
                approved = self.callbacks.on_replan_approval_required(reason)
                if not approved:
                    self._log("Replan not approved - pausing")
                    self.status = OrchestratorStatus.WAITING_FOR_HUMAN
                    return

        context = {"learnings": [], "reason": reason}
        recent = self.state_manager.get_recent_iterations(10)
        for r in recent:
            if r.get("learnings"):
                learnings = json.loads(r["learnings"])
                context["learnings"].extend(learnings)

        new_tasks = self.plan_manager.revise_plan(reason, context=context)
        self._log(f"Plan revised with {len(new_tasks)} new tasks")
        if self.callbacks.on_plan_revised:
            self.callbacks.on_plan_revised(reason, new_tasks)

    def _summarize_evidence(self, task: Task, artifacts: List[Artifact]) -> tuple[str, List[str]]:
        artifact_refs = [a.file_path for a in artifacts if a.file_path]
        side_effects = self.state_manager.get_task_side_effects(task.id)
        receipt_refs = []
        for effect in side_effects:
            key = effect.get("idempotency_key") or effect.get("id")
            if key:
                receipt_refs.append(str(key))

        has_artifact = bool(artifact_refs)
        has_receipt = bool(receipt_refs)

        if has_artifact and has_receipt:
            evidence_type = "hybrid"
        elif has_receipt:
            evidence_type = "receipt"
        elif has_artifact:
            evidence_type = "artifact"
        else:
            evidence_type = "programmatic"

        return evidence_type, list({*artifact_refs, *receipt_refs})

    def _should_exit(self) -> bool:
        return self._should_stop or self.status in {
            OrchestratorStatus.COMPLETE,
            OrchestratorStatus.FAILED,
        }

    def _generate_summary(self) -> Dict[str, Any]:
        stats = self.state_manager.get_completion_stats()
        return {
            "status": self.status.value,
            "original_request": self.state_manager.get_original_request(),
            "total_iterations": self.total_iterations,
            "task_stats": stats,
            "tasks_completed": stats.get("complete", 0),
            "tasks_failed": stats.get("failed", 0),
            "tasks_pending": stats.get("pending", 0),
            "is_complete": self.state_manager.is_plan_complete(),
            "workspace_path": str(self.workspace_path),
            "artifacts_dir": str(self.workspace_path / ".urw" / "artifacts"),
            "final_checkpoint": self.state_manager.checkpointer.get_current_sha(),
        }

    def _log(self, message: str) -> None:
        if self.config.verbose:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[URW {timestamp}] {message}")
        if self.callbacks.on_progress:
            self.callbacks.on_progress(message)


async def run_universal_task(
    request: str,
    agent_loop: AgentLoopInterface,
    llm_client: Any,
    workspace_path: Optional[Path] = None,
    config: Optional[URWConfig] = None,
) -> Dict[str, Any]:
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
    on_plan_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
    config: Optional[URWConfig] = None,
) -> URWOrchestrator:
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
