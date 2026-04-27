"""
URW (Universal Ralph Wrapper) integration package.

This package contains the URW outer-loop orchestrator, state manager,
completion evaluator, decomposer, and adapter hooks for the Universal Agent.
"""

from .decomposer import (
    DECOMPOSITION_TEMPLATES,
    Decomposer,
    HybridDecomposer,
    LLMDecomposer,
    PlanManager,
    TemplateDecomposer,
    estimate_task_complexity,
    validate_task_graph,
)
from .evaluation_policy import (
    DEFAULT_EVALUATION_POLICY,
    TEMPLATE_EVALUATION_POLICIES,
    VERIFICATION_TYPE_DEFAULTS,
    EvaluationPolicySchema,
    get_policy_summary,
    resolve_evaluation_policy,
)
from .evaluator import (
    BinaryCheckEvaluator,
    CompositeEvaluator,
    ConstraintEvaluator,
    EvaluationResult,
    Evaluator,
    LLMJudgeEvaluator,
    create_default_evaluator,
    quick_evaluate,
)
from .integration import (
    BaseAgentAdapter,
    MockAgentAdapter,
    UniversalAgentAdapter,
    create_adapter_for_system,
)
from .orchestrator import (
    AgentExecutionResult,
    AgentLoopInterface,
    OrchestratorStatus,
    URWCallbacks,
    URWConfig,
    URWOrchestrator,
    create_orchestrator_with_callbacks,
    run_universal_task,
)
from .state import (
    Artifact,
    ArtifactType,
    CompletionConfidence,
    GitCheckpointer,
    IterationResult,
    Task,
    TaskStatus,
    URWStateManager,
)

__all__ = [
    "Task",
    "TaskStatus",
    "Artifact",
    "ArtifactType",
    "CompletionConfidence",
    "IterationResult",
    "URWStateManager",
    "GitCheckpointer",
    "Decomposer",
    "TemplateDecomposer",
    "LLMDecomposer",
    "HybridDecomposer",
    "PlanManager",
    "DECOMPOSITION_TEMPLATES",
    "estimate_task_complexity",
    "validate_task_graph",
    "EvaluationResult",
    "Evaluator",
    "BinaryCheckEvaluator",
    "ConstraintEvaluator",
    "LLMJudgeEvaluator",
    "CompositeEvaluator",
    "create_default_evaluator",
    "quick_evaluate",
    "URWOrchestrator",
    "OrchestratorStatus",
    "URWConfig",
    "URWCallbacks",
    "AgentLoopInterface",
    "AgentExecutionResult",
    "run_universal_task",
    "create_orchestrator_with_callbacks",
    "BaseAgentAdapter",
    "UniversalAgentAdapter",
    "MockAgentAdapter",
    "create_adapter_for_system",
    # Evaluation policy
    "EvaluationPolicySchema",
    "DEFAULT_EVALUATION_POLICY",
    "VERIFICATION_TYPE_DEFAULTS",
    "TEMPLATE_EVALUATION_POLICIES",
    "resolve_evaluation_policy",
    "get_policy_summary",
    # Context summarization
    "ContextCheckpoint",
    "ContextSummarizer",
    "pre_compact_checkpoint_hook",
    # Phase planning
    "Phase",
    "PhaseStatus",
    "PhasePlanner",
]

# Context summarization (lazy import to avoid circular deps)
from .context_summarizer import (
    ContextCheckpoint,
    ContextSummarizer,
    pre_compact_checkpoint_hook,
)

# Phase planning
from .phase_planner import (
    Phase,
    PhasePlanner,
    PhaseStatus,
)
