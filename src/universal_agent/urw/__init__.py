"""
URW (Universal Ralph Wrapper) integration package.

This package contains the URW outer-loop orchestrator, state manager,
completion evaluator, decomposer, and adapter hooks for the Universal Agent.
"""

from .state import (
    Task,
    TaskStatus,
    Artifact,
    ArtifactType,
    CompletionConfidence,
    IterationResult,
    URWStateManager,
    GitCheckpointer,
)
from .decomposer import (
    Decomposer,
    TemplateDecomposer,
    LLMDecomposer,
    HybridDecomposer,
    PlanManager,
    DECOMPOSITION_TEMPLATES,
    estimate_task_complexity,
    validate_task_graph,
)
from .evaluator import (
    EvaluationResult,
    Evaluator,
    BinaryCheckEvaluator,
    ConstraintEvaluator,
    LLMJudgeEvaluator,
    CompositeEvaluator,
    create_default_evaluator,
    quick_evaluate,
)
from .orchestrator import (
    URWOrchestrator,
    OrchestratorStatus,
    URWConfig,
    URWCallbacks,
    AgentLoopInterface,
    AgentExecutionResult,
    run_universal_task,
    create_orchestrator_with_callbacks,
)
from .integration import (
    BaseAgentAdapter,
    UniversalAgentAdapter,
    MockAgentAdapter,
    create_adapter_for_system,
)
from .evaluation_policy import (
    EvaluationPolicySchema,
    DEFAULT_EVALUATION_POLICY,
    VERIFICATION_TYPE_DEFAULTS,
    TEMPLATE_EVALUATION_POLICIES,
    resolve_evaluation_policy,
    get_policy_summary,
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
]
