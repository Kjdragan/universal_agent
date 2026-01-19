"""
URW Plan Schema

Pydantic models for structured plan extraction from interview phase.
Based on interview.md design.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Status of a task or phase."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskPriority(str, Enum):
    """Priority level for tasks."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AtomicTask(BaseModel):
    """
    A single atomic task that can be completed in one agent session.
    
    Atomic tasks should be small enough to complete within a single
    context window iteration of the multi-agent system.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(..., max_length=200, description="Short task name")
    description: str = Field(default="", description="Detailed task description")
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM)
    dependencies: List[str] = Field(
        default_factory=list,
        description="IDs of tasks that must complete before this one"
    )
    estimated_duration_minutes: Optional[int] = Field(
        default=None,
        description="Estimated time to complete in minutes"
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context needed for this task"
    )
    output: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Output/artifacts produced by this task"
    )
    success_criteria: List[str] = Field(
        default_factory=list,
        description="Measurable specific criteria for success vs failure"
    )
    use_case: str = Field(
        default="",
        description="Specific intent or use case for the tool router"
    )

class Phase(BaseModel):
    """
    A phase groups related atomic tasks that should be executed together.
    
    Each phase represents one "user prompt" fed to the multi-agent system,
    designed to fit within a single context window.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(..., description="Phase name (e.g., 'Research', 'Implementation')")
    description: str = Field(default="", description="What this phase accomplishes")
    order: int = Field(..., ge=0, description="Execution order (0-indexed)")
    tasks: List[AtomicTask] = Field(default_factory=list)
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    session_path: Optional[str] = Field(
        default=None,
        description="Path to session directory for this phase"
    )
    prompt: Optional[str] = Field(
        default=None,
        description="The prompt to feed to multi-agent system for this phase"
    )

    def get_expected_artifacts(self) -> List[str]:
        """Extract expected artifacts from task outputs."""
        artifacts = []
        for task in self.tasks:
            if task.context.get("expected_output"):
                artifacts.append(task.context["expected_output"])
        return artifacts if artifacts else [f"Complete {self.name}"]


class Plan(BaseModel):
    """
    Complete execution plan generated from interview phase.
    
    Contains phases with atomic tasks, global context,
    and tracking metadata.
    """
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(..., description="Plan name derived from massive request")
    description: str = Field(default="", description="Summary of what the plan accomplishes")
    massive_request: str = Field(default="", description="Original user request")
    phases: List[Phase] = Field(default_factory=list)
    version: str = Field(default="1.0.0")
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    harness_id: Optional[str] = Field(default=None, description="Associated harness run ID")
    global_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Context shared across all phases"
    )
    interview_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Data collected during interview"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}

    def get_pending_phases(self) -> List[Phase]:
        """Get phases that haven't started yet."""
        return [p for p in self.phases if p.status == TaskStatus.PENDING]

    def get_current_phase(self) -> Optional[Phase]:
        """Get the phase currently in progress."""
        for phase in self.phases:
            if phase.status == TaskStatus.IN_PROGRESS:
                return phase
        return None

    def get_next_phase(self) -> Optional[Phase]:
        """Get the next pending phase."""
        pending = self.get_pending_phases()
        return pending[0] if pending else None

    def mark_phase_complete(self, phase_id: str) -> None:
        """Mark a phase as complete and update plan status."""
        for phase in self.phases:
            if phase.id == phase_id:
                phase.status = TaskStatus.COMPLETED
                break
        
        # Update plan status if all phases complete
        if all(p.status == TaskStatus.COMPLETED for p in self.phases):
            self.status = TaskStatus.COMPLETED
        
        self.updated_at = datetime.utcnow()

    def total_tasks(self) -> int:
        """Count total atomic tasks across all phases."""
        return sum(len(p.tasks) for p in self.phases)


def plan_to_mission_json(plan: Plan) -> dict:
    """
    Convert a Plan to mission.json format for integration with existing harness.
    
    The existing harness expects mission.json with:
    - mission_root: The overall goal
    - status: 'PLANNING' or 'IN_PROGRESS'
    - clarifications: User answers from interview
    - tasks: Array of task objects with id, description, context, etc.
    """
    tasks = []
    task_num = 1
    
    for phase in plan.phases:
        for task in phase.tasks:
            # Combine explicit success_criteria with context
            criteria = task.success_criteria
            if not criteria and "success_criteria" in task.context:
                criteria = task.context["success_criteria"]
            if isinstance(criteria, str):
                criteria = [criteria]
            
            # Use explicit use_case or fallback
            use_case = task.use_case
            if not use_case:
                use_case = task.context.get("use_case", f"Phase {phase.order + 1}: {phase.name}")
                
            tasks.append({
                "id": f"task_{task_num:03d}",
                "description": task.name,
                "context": task.description,
                "use_case": use_case,
                "success_criteria": criteria,
                "output_artifacts": task.context.get("output_artifacts", []),
                "status": "PENDING",
                "phase": phase.name,
                "phase_order": phase.order,
            })
            task_num += 1
    
    return {
        "mission_root": plan.massive_request or plan.description or plan.name,
        "status": "PLANNING",
        "clarifications": plan.interview_data,
        "tasks": tasks,
        "plan_id": str(plan.id),
        "created_at": plan.created_at.isoformat(),
    }
