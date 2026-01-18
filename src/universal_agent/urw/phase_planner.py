"""
Phase Planner - Groups atomic tasks into execution phases

The PhasePlanner addresses the over-decomposition issue by grouping atomic tasks
into phases that match what the multi-agent system can handle in a single iteration.

Key concepts:
- Phase = One harness iteration = fresh agent context
- Tasks within a phase are executed together by the multi-agent
- Phase boundaries occur at natural work points (after sub-agent completion, side effects, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any, Set
from datetime import datetime

from .state import Task, TaskStatus


class PhaseStatus(Enum):
    """Status of a phase."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"
    PARTIAL = "partial"  # Some tasks complete, others need next phase


@dataclass
class Phase:
    """
    A group of atomic tasks to be executed together in one harness iteration.
    
    Each phase corresponds to a single agent context window.
    """
    phase_id: str
    name: str
    description: str = ""
    
    # Tasks in this phase (executed together)
    task_ids: List[str] = field(default_factory=list)
    
    # Execution state
    status: PhaseStatus = PhaseStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # Results
    completed_task_ids: List[str] = field(default_factory=list)
    failed_task_ids: List[str] = field(default_factory=list)
    
    # Context for this phase (from previous phases)
    input_context: Dict[str, Any] = field(default_factory=dict)
    output_context: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_complete(self) -> bool:
        return self.status == PhaseStatus.COMPLETE
    
    @property
    def task_count(self) -> int:
        return len(self.task_ids)
    
    def mark_started(self):
        self.status = PhaseStatus.IN_PROGRESS
        self.started_at = datetime.utcnow().isoformat()
    
    def mark_complete(self, completed_ids: List[str], failed_ids: List[str] = None):
        self.completed_task_ids = completed_ids
        self.failed_task_ids = failed_ids or []
        self.completed_at = datetime.utcnow().isoformat()
        
        if not self.failed_task_ids and set(completed_ids) >= set(self.task_ids):
            self.status = PhaseStatus.COMPLETE
        elif self.failed_task_ids:
            self.status = PhaseStatus.FAILED
        else:
            self.status = PhaseStatus.PARTIAL
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase_id": self.phase_id,
            "name": self.name,
            "description": self.description,
            "task_ids": self.task_ids,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "completed_task_ids": self.completed_task_ids,
            "failed_task_ids": self.failed_task_ids,
            "input_context": self.input_context,
            "output_context": self.output_context,
        }


class PhasePlanner:
    """
    Groups atomic tasks into phases based on natural work boundaries.
    
    Design principles:
    1. Respect sub-agent boundaries (sub-agent = atomic unit, don't break mid-execution)
    2. Group related tasks that can execute in one agent context
    3. Break at natural boundaries (side effects, deliverables, research→synthesis)
    4. Consider task dependencies
    
    Assessment modes:
    - Pythonic (fast): Heuristics for simple cases (≤4 tasks, linear chains)
    - LLM (accurate): Semantic assessment for complex cases (5+ tasks)
    """
    
    # Tasks that represent natural phase boundaries (break AFTER these)
    PHASE_BOUNDARY_PATTERNS = {
        "side_effect": ["send", "email", "publish", "deploy", "upload"],
        "deliverable": ["report", "output", "final", "deliver"],
        "transition": ["analyze", "synthesize"],  # research→synthesis boundary
    }
    
    # Maximum tasks per phase (soft limit, can be exceeded for dependencies)
    DEFAULT_MAX_TASKS_PER_PHASE = 24
    
    # Token budget estimation (Sonnet 200k support)
    CONTEXT_BUDGET_TOKENS = 160000
    
    # Estimated tokens per task type
    TASK_TOKEN_ESTIMATES = {
        "research": 10000,   # Research generates lots of output
        "search": 8000,     # Search results
        "gather": 5000,     # Gathering data
        "analyze": 5000,     # Analysis consumes but produces less
        "synthesize": 6000,  # Synthesis is more focused
        "report": 10000,     # Reports can be large
        "email": 2000,       # Emails are small
        "default": 5000,     # Default estimate
    }
    
    def __init__(
        self, 
        max_tasks_per_phase: int = None,
        llm_client: Any = None,
        use_llm_assessment: bool = True,
        llm_model: str = None,
    ):
        """
        Initialize PhasePlanner.
        
        Args:
            max_tasks_per_phase: Soft limit on tasks per phase
            llm_client: Anthropic client for LLM assessment (optional)
            use_llm_assessment: Whether to use LLM for complex cases
            llm_model: Model to use for assessment (defaults to fast model)
        """
        self.max_tasks_per_phase = max_tasks_per_phase or self.DEFAULT_MAX_TASKS_PER_PHASE
        self.llm_client = llm_client
        self.use_llm_assessment = use_llm_assessment and llm_client is not None
        self.llm_model = llm_model or "claude-sonnet-4-20250514"

    
    def plan_phases(
        self, 
        tasks: List[Task],
        single_phase_mode: bool = False,
        use_llm: bool = None,  # Override instance setting
    ) -> List[Phase]:
        """
        Group tasks into phases.
        
        Args:
            tasks: List of atomic tasks from decomposition
            single_phase_mode: If True, put ALL tasks in one phase (for simple queries)
            use_llm: Override to force/disable LLM assessment
            
        Returns:
            List of Phase objects
        """
        if not tasks:
            return []
        
        # Single phase mode - everything in one phase
        if single_phase_mode:
            return [self._create_single_phase(tasks)]
        
        # Determine assessment mode
        should_use_llm = use_llm if use_llm is not None else self.use_llm_assessment
        complexity = self.estimate_complexity(tasks)
        
        # Simple cases: use heuristics (fast)
        if complexity == "simple":
            if self._should_be_single_phase(tasks):
                return [self._create_single_phase(tasks)]
            return self._plan_multi_phase(tasks)
        
        # Complex cases: try LLM assessment if available
        if should_use_llm and complexity in ("moderate", "complex"):
            llm_phases = self._plan_phases_with_llm(tasks)
            if llm_phases:
                return llm_phases
        
        # Fallback to heuristic planning
        if self._should_be_single_phase(tasks):
            return [self._create_single_phase(tasks)]
        return self._plan_multi_phase(tasks)
    
    def estimate_task_tokens(self, task: Task) -> int:
        """
        Estimate token usage for a task based on its type.
        
        This helps determine if tasks will fit in a single context window.
        """
        title_lower = task.title.lower()
        desc_lower = (task.description or "").lower()
        combined = title_lower + " " + desc_lower
        
        for task_type, tokens in self.TASK_TOKEN_ESTIMATES.items():
            if task_type in combined:
                return tokens
        
        return self.TASK_TOKEN_ESTIMATES["default"]
    
    def estimate_phase_tokens(self, tasks: List[Task]) -> int:
        """Estimate total tokens for a set of tasks."""
        return sum(self.estimate_task_tokens(t) for t in tasks)
    
    def _plan_phases_with_llm(self, tasks: List[Task]) -> Optional[List[Phase]]:
        """
        Use LLM to assess optimal phase groupings.
        
        The LLM receives task information and suggests how to group them
        based on semantic understanding of dependencies, complexity, and
        context window constraints.
        """
        if not self.llm_client:
            return None
        
        try:
            # Build task descriptions for LLM
            task_info = []
            for i, task in enumerate(tasks):
                deps = task.depends_on if task.depends_on else ["none"]
                token_est = self.estimate_task_tokens(task)
                task_info.append(
                    f"{i+1}. ID: {task.id}\n"
                    f"   Title: {task.title}\n"
                    f"   Description: {task.description or 'N/A'}\n"
                    f"   Dependencies: {', '.join(deps)}\n"
                    f"   Est. tokens: ~{token_est:,}"
                )
            
            tasks_text = "\n".join(task_info)
            
            prompt = f"""You are a task phase planner for an AI agent system.

## Context
- Each phase = one agent execution with ~100K token context budget
- Tasks in a phase execute together in sequence
- Later phases receive summarized context from earlier phases
- Goal: Group tasks to minimize phases while respecting constraints

## Tasks to Plan
{tasks_text}

## Constraints
1. Respect dependencies (a task's dependencies must be in same or earlier phase)
2. Keep total tokens per phase under 80,000 (leave room for context)
3. Break at natural boundaries: research→analysis, analysis→output, before side effects (email/upload)
4. Prefer fewer phases when possible (less context loss between phases)

## Response Format
Respond with ONLY a JSON object in this exact format:
{{
  "phases": [
    {{
      "phase_number": 1,
      "name": "Phase name",
      "task_ids": ["task_id_1", "task_id_2"],
      "rationale": "Brief reason for grouping"
    }}
  ],
  "reasoning": "Overall reasoning for the phase structure"
}}"""

            # Call LLM
            response = self.llm_client.messages.create(
                model=self.llm_model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Parse response
            response_text = response.content[0].text
            
            # Extract JSON from response
            import json
            import re
            
            # Try to find JSON in response
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if not json_match:
                return None
            
            result = json.loads(json_match.group())
            
            # Convert to Phase objects
            phases = []
            for p in result.get("phases", []):
                phase = Phase(
                    phase_id=f"phase_{p['phase_number']}",
                    name=p.get("name", f"Phase {p['phase_number']}"),
                    description=p.get("rationale", ""),
                    task_ids=p.get("task_ids", []),
                )
                phases.append(phase)
            
            # Validate phases cover all tasks
            all_phase_tasks = set()
            for phase in phases:
                all_phase_tasks.update(phase.task_ids)
            
            all_task_ids = {t.id for t in tasks}
            if all_phase_tasks != all_task_ids:
                # LLM missed some tasks, fall back to heuristics
                return None
            
            return phases
            
        except Exception as e:
            # Log error but don't fail - fall back to heuristics
            print(f"[PhasePlanner] LLM assessment failed: {e}")
            return None
    
    def _should_be_single_phase(self, tasks: List[Task]) -> bool:
        """
        Determine if tasks should all be in a single phase.
        
        Criteria for single phase:
        - Small number of tasks (≤4)
        - No complex side effects
        - Linear dependency chain
        - Estimated tokens under budget
        """
        # Few tasks → single phase
        if len(tasks) <= 4:
            # Also check token budget
            if self.estimate_phase_tokens(tasks) < self.CONTEXT_BUDGET_TOKENS:
                return True
        
        # All tasks have simple linear dependencies → single phase
        if self._is_linear_chain(tasks):
            if self.estimate_phase_tokens(tasks) < self.CONTEXT_BUDGET_TOKENS:
                return True
        
        return False
    
    def _is_linear_chain(self, tasks: List[Task]) -> bool:
        """Check if tasks form a simple linear chain."""
        for i, task in enumerate(tasks):
            # First task should have no deps or only one
            if i == 0:
                if len(task.depends_on) > 0:
                    return False
            else:
                # Each subsequent task should depend only on the previous
                if len(task.depends_on) != 1:
                    return False
        return True
    
    def _create_single_phase(self, tasks: List[Task]) -> Phase:
        """Create a single phase containing all tasks."""
        # Determine name from tasks
        task_types = [t.title.lower() for t in tasks]
        
        if any("research" in t for t in task_types):
            name = "Research and Report"
        elif any("email" in t for t in task_types):
            name = "Email Task"
        elif any("analyze" in t for t in task_types):
            name = "Analysis Task"
        else:
            name = "Complete Task"
        
        return Phase(
            phase_id="phase_1_complete",
            name=name,
            description="All tasks executed in single phase",
            task_ids=[t.id for t in tasks],
        )
    
    def _plan_multi_phase(self, tasks: List[Task]) -> List[Phase]:
        """
        Plan multiple phases for complex task sets.
        
        Algorithm:
        1. Build dependency graph
        2. Group tasks respecting dependencies
        3. Break at natural boundaries
        """
        phases: List[Phase] = []
        remaining_tasks = list(tasks)
        task_map = {t.id: t for t in tasks}
        completed_task_ids: Set[str] = set()
        phase_num = 0
        
        while remaining_tasks:
            phase_num += 1
            phase_tasks: List[Task] = []
            
            # Find tasks whose dependencies are satisfied
            for task in remaining_tasks[:]:
                deps_satisfied = all(
                    dep_id in completed_task_ids 
                    for dep_id in task.depends_on
                )
                
                if deps_satisfied:
                    phase_tasks.append(task)
                    remaining_tasks.remove(task)
                    
                    # Check if this is a natural boundary
                    if self._is_phase_boundary(task):
                        break
                    
                    # Respect max tasks per phase
                    if len(phase_tasks) >= self.max_tasks_per_phase:
                        break
            
            if not phase_tasks:
                # No progress - circular dependency or error
                break
            
            # Create phase
            phase = Phase(
                phase_id=f"phase_{phase_num}",
                name=self._generate_phase_name(phase_tasks, phase_num),
                description=self._generate_phase_description(phase_tasks),
                task_ids=[t.id for t in phase_tasks],
            )
            phases.append(phase)
            
            # Mark tasks as "completed" for dependency checking
            for t in phase_tasks:
                completed_task_ids.add(t.id)
        
        return phases
    
    def _is_phase_boundary(self, task: Task) -> bool:
        """Check if task represents a natural phase boundary."""
        title_lower = task.title.lower()
        desc_lower = (task.description or "").lower()
        
        for category, patterns in self.PHASE_BOUNDARY_PATTERNS.items():
            for pattern in patterns:
                if pattern in title_lower or pattern in desc_lower:
                    return True
        
        return False
    
    def _generate_phase_name(self, tasks: List[Task], phase_num: int) -> str:
        """Generate a descriptive name for the phase."""
        if len(tasks) == 1:
            return tasks[0].title
        
        # Find common theme
        titles = [t.title.lower() for t in tasks]
        
        if any("research" in t or "search" in t or "gather" in t for t in titles):
            return f"Phase {phase_num}: Research & Gathering"
        elif any("analyze" in t or "synthesize" in t for t in titles):
            return f"Phase {phase_num}: Analysis & Synthesis"
        elif any("report" in t or "write" in t for t in titles):
            return f"Phase {phase_num}: Report Generation"
        elif any("email" in t or "send" in t for t in titles):
            return f"Phase {phase_num}: Delivery"
        else:
            return f"Phase {phase_num}: {tasks[0].title}"
    
    def _generate_phase_description(self, tasks: List[Task]) -> str:
        """Generate description from tasks."""
        task_descs = [t.title for t in tasks[:3]]
        if len(tasks) > 3:
            task_descs.append(f"... and {len(tasks) - 3} more")
        return "Tasks: " + ", ".join(task_descs)
    
    def estimate_complexity(self, tasks: List[Task]) -> str:
        """
        Estimate overall complexity to help decide on phase strategy.
        
        Returns: "simple", "moderate", "complex"
        """
        if len(tasks) <= 2:
            return "simple"
        elif len(tasks) <= 5:
            # Check for complex patterns
            has_side_effects = any(
                any(p in (t.title + " " + (t.description or "")).lower() 
                    for p in self.PHASE_BOUNDARY_PATTERNS["side_effect"])
                for t in tasks
            )
            if has_side_effects:
                return "moderate"
            return "simple"
        else:
            return "complex"
    
    def replan_from_incomplete(
        self, 
        phase: Phase, 
        completed_tasks: List[str],
        all_tasks: List[Task]
    ) -> List[Phase]:
        """
        Create new phases from incomplete phase.
        
        When a phase doesn't complete all its tasks, we need to create
        follow-up phases for the remaining work.
        """
        remaining_ids = set(phase.task_ids) - set(completed_tasks)
        task_map = {t.id: t for t in all_tasks}
        remaining_tasks = [task_map[tid] for tid in remaining_ids if tid in task_map]
        
        if not remaining_tasks:
            return []
        
        # Replan with remaining tasks
        return self._plan_multi_phase(remaining_tasks)
