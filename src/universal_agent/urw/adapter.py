"""
Adapter module for converting Harness objects to State objects.
Bridges schema differences between plan_schema.AtomicTask and state.Task.
"""

from typing import List, Dict, Any, Optional

# Harness imports
from .plan_schema import AtomicTask

# State imports (for evaluation)
from .state import Task, TaskStatus

class HarnessAdapter:
    """Adapts Harness definition objects to URW State objects."""
    
    @staticmethod
    def atomic_task_to_state_task(atomic_task: AtomicTask, phase_id: str = "phase_0") -> Task:
        """
        Convert an AtomicTask (Harness) to a state.Task (Evaluator).
        
        Logic:
        - `name` -> `title`
        - `description` -> `description`
        - `success_criteria`:
          - If string starts with "file:" or "check:" -> `binary_checks`
          - Otherwise -> Added to `evaluation_rubric`
        """
        binary_checks: List[str] = []
        rubric_items: List[str] = []
        
        if atomic_task.success_criteria:
            for criterion in atomic_task.success_criteria:
                # Simple heuristic for binary checks
                normalized = criterion.lower().strip()
                if normalized.startswith(("file:", "exists:", "check:", "file_exists:")):
                    # Clean up prefix for standard evaluator format if needed
                    # evaluator.py supports "file_exists:", "artifact_exists:", "side_effect:", "contains:"
                    if normalized.startswith("file:"):
                         binary_checks.append(f"file_exists:{criterion[5:].strip()}")
                    elif normalized.startswith("file_exists:"):
                         binary_checks.append(criterion)
                    else:
                         binary_checks.append(criterion)
                else:
                    rubric_items.append(criterion)
        
        # Combine rubric items into a single string
        evaluation_rubric = "\n".join(f"- {item}" for item in rubric_items) if rubric_items else None
        
        # Determine verification type
        verification_type = "composite"
        if binary_checks and not rubric_items:
            verification_type = "binary"
        elif rubric_items and not binary_checks:
            verification_type = "qualitative"
            
        return Task(
            id=atomic_task.id,
            title=atomic_task.name,
            description=f"{atomic_task.description}\n\nUse Case: {atomic_task.use_case}",
            status=TaskStatus.PENDING,
            verification_type=verification_type,
            binary_checks=binary_checks,
            constraints=[],  # Could parse from success_criteria complexity if needed
            evaluation_rubric=evaluation_rubric,
            minimum_acceptable_score=0.65, # Default
        )
