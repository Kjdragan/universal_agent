"""
Evaluation Policy Configuration Schema

Centralized configuration for evaluation policy defaults and resolution logic.
This module defines:
1. Schema documentation for all policy options
2. Global defaults
3. Per-verification-type defaults
4. Per-template defaults
5. Resolution logic to merge all levels
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .state import Task


@dataclass
class EvaluationPolicySchema:
    """
    Complete schema for evaluation policy configuration.
    
    Each field documents a policy option that can be set at three levels:
    1. Global (URWConfig.evaluation_policy or CLI flags)
    2. Template (DECOMPOSITION_TEMPLATES[template]["evaluation_policy"])
    3. Task (Task.evaluation_policy)
    
    Resolution order: Task overrides Template overrides Global overrides Defaults.
    """
    
    # Binary check requirements
    require_binary: Optional[bool] = None
    """
    Whether binary checks must pass for task completion.
    - True: All binary checks must pass
    - False: Binary checks are recorded but don't gate completion
    - None: Auto-detect from task.binary_checks presence
    """
    
    # Constraint requirements
    require_constraints: Optional[bool] = None
    """
    Whether constraint checks must pass for task completion.
    - True: All constraints must pass
    - False: Constraints are recorded but don't gate completion
    - None: Auto-detect from task.constraints presence
    """
    
    # Qualitative/LLM evaluation requirements
    require_qualitative: Optional[bool] = None
    """
    Whether qualitative (LLM-as-judge) evaluation is required.
    - True: LLM evaluation must pass minimum score
    - False: Skip LLM evaluation entirely
    - None: Auto-detect from task.evaluation_rubric presence
    """
    
    qualitative_min_score: Optional[float] = None
    """
    Minimum score (0.0-1.0) required to pass qualitative evaluation.
    Default: 0.6 for qualitative-focused tasks, 0.7 for composite
    """
    
    # Overall score threshold (optional)
    overall_min_score: Optional[float] = None
    """
    Optional minimum combined score (average of all evaluations).
    - If set: Task fails if overall score < this threshold
    - If None: Only individual check requirements gate completion
    """
    
    # Advanced options (future)
    binary_pass_ratio: float = 1.0
    """
    Ratio of binary checks that must pass (0.0-1.0).
    Default: 1.0 (all must pass). Set lower for lenient evaluation.
    Reserved for future implementation.
    """
    
    constraint_pass_ratio: float = 1.0
    """
    Ratio of constraints that must pass (0.0-1.0).
    Default: 1.0 (all must pass). Set lower for lenient evaluation.
    Reserved for future implementation.
    """


# =============================================================================
# DEFAULT POLICIES
# =============================================================================

DEFAULT_EVALUATION_POLICY: Dict[str, Any] = {
    # By default, let task configuration determine requirements
    "require_binary": None,
    "require_constraints": None,
    "require_qualitative": None,
    
    # Default minimum scores
    "qualitative_min_score": 0.65,
    "overall_min_score": None,  # Disabled by default
    
    # Pass ratios (reserved for future)
    "binary_pass_ratio": 1.0,
    "constraint_pass_ratio": 1.0,
}


# Per-verification-type defaults
VERIFICATION_TYPE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "binary": {
        "require_binary": True,
        "require_constraints": False,
        "require_qualitative": False,
    },
    "constraint": {
        "require_binary": False,
        "require_constraints": True,
        "require_qualitative": False,
    },
    "qualitative": {
        "require_binary": False,
        "require_constraints": False,
        "require_qualitative": True,
        "qualitative_min_score": 0.65,
    },
    "composite": {
        # Auto-detect from task definition
        "require_binary": None,
        "require_constraints": None,
        "require_qualitative": None,
    },
}


# =============================================================================
# TEMPLATE-LEVEL POLICIES
# =============================================================================

TEMPLATE_EVALUATION_POLICIES: Dict[str, Dict[str, Any]] = {
    "research_report": {
        # Research is exploratory - be lenient on qualitative
        "qualitative_min_score": 0.65,
        "require_qualitative": True,
    },
    "email_outreach": {
        # Emails need quality control
        "qualitative_min_score": 0.65,
        "require_qualitative": True,
    },
    "document_analysis": {
        # Analysis should be thorough
        "qualitative_min_score": 0.65,
        "require_qualitative": True,
    },
    "data_processing": {
        # Data work is more binary - either correct or not
        "require_binary": True,
        "require_constraints": True,
        "require_qualitative": False,
    },
    "content_creation": {
        # Content needs both structure and quality
        "qualitative_min_score": 0.65,
        "require_qualitative": True,
    },
}


# =============================================================================
# TASK-LEVEL POLICY DEFAULTS (per task within templates)
# =============================================================================

TASK_POLICY_OVERRIDES: Dict[str, Dict[str, Any]] = {
    # Research tasks that are exploratory
    "scope": {"qualitative_min_score": 0.65},  # Early planning is flexible
    "gather": {"require_qualitative": False},  # Gathering is more mechanical
    
    # Final output tasks need higher quality
    "report": {"qualitative_min_score": 0.65},
    "output": {"qualitative_min_score": 0.65},
    
    # Side-effect tasks are binary
    "send": {"require_binary": True, "require_qualitative": False},
}


# =============================================================================
# POLICY RESOLUTION
# =============================================================================

def resolve_evaluation_policy(
    task: "Task",
    global_policy: Optional[Dict[str, Any]] = None,
    template_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve the final evaluation policy for a task.
    
    Resolution order (later overrides earlier):
    1. DEFAULT_EVALUATION_POLICY
    2. VERIFICATION_TYPE_DEFAULTS[task.verification_type]
    3. TEMPLATE_EVALUATION_POLICIES[template_name] (if provided)
    4. TASK_POLICY_OVERRIDES[task_suffix] (if matching)
    5. global_policy (URWConfig.evaluation_policy)
    6. task.evaluation_policy
    
    Args:
        task: The task to resolve policy for
        global_policy: Global overrides from URWConfig
        template_name: Optional template name for template-level defaults
    
    Returns:
        Fully resolved policy dict
    """
    policy: Dict[str, Any] = dict(DEFAULT_EVALUATION_POLICY)
    
    # 2. Verification type defaults
    verification_type = task.verification_type or "composite"
    if verification_type in VERIFICATION_TYPE_DEFAULTS:
        for key, value in VERIFICATION_TYPE_DEFAULTS[verification_type].items():
            if value is not None:
                policy[key] = value
    
    # 3. Template-level defaults
    if template_name and template_name in TEMPLATE_EVALUATION_POLICIES:
        policy.update(TEMPLATE_EVALUATION_POLICIES[template_name])
    
    # 4. Task suffix overrides (e.g., "scope", "report")
    task_suffix = task.id.split("_")[-1] if "_" in task.id else None
    if task_suffix and task_suffix in TASK_POLICY_OVERRIDES:
        for key, value in TASK_POLICY_OVERRIDES[task_suffix].items():
            if value is not None:
                policy[key] = value
    
    # 5. Global policy overrides
    if global_policy:
        for key, value in global_policy.items():
            if value is not None:
                policy[key] = value
    
    # 6. Task-level policy overrides (highest priority)
    if task.evaluation_policy:
        for key, value in task.evaluation_policy.items():
            if value is not None:
                policy[key] = value
    
    # Auto-detect requirements if still None
    if policy.get("require_binary") is None:
        policy["require_binary"] = bool(task.binary_checks)
    if policy.get("require_constraints") is None:
        policy["require_constraints"] = bool(task.constraints)
    if policy.get("require_qualitative") is None:
        policy["require_qualitative"] = bool(task.evaluation_rubric)
    
    # Ensure qualitative_min_score has a fallback
    if policy.get("qualitative_min_score") is None:
        policy["qualitative_min_score"] = task.minimum_acceptable_score or 0.65
    
    return policy


def get_policy_summary(policy: Dict[str, Any]) -> str:
    """Generate a human-readable summary of the policy."""
    parts = []
    
    if policy.get("require_binary"):
        parts.append("binary=required")
    if policy.get("require_constraints"):
        parts.append("constraints=required")
    if policy.get("require_qualitative"):
        qual_min = policy.get("qualitative_min_score", 0.65)
        parts.append(f"qualitative≥{qual_min:.0%}")
    if policy.get("overall_min_score"):
        parts.append(f"overall≥{policy['overall_min_score']:.0%}")
    
    return ", ".join(parts) if parts else "auto-detect"
