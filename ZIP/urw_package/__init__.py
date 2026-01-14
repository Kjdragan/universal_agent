"""
Universal Ralph Wrapper (URW)

A harness for long-running universal agent tasks, inspired by Anthropic's
Ralph loop pattern but adapted for non-code tasks like research, analysis,
communication, and content creation.

Key Components:
    - URWOrchestrator: The outer loop that manages task execution
    - URWStateManager: Deterministic state persistence (SQLite + Git + Files)
    - PlanManager: Task decomposition and dynamic re-planning
    - CompositeEvaluator: Multi-strategy completion evaluation

Quick Start:
    ```python
    from urw_package import URWOrchestrator, URWConfig, MockAgentAdapter
    
    # Create adapter for your agent system
    adapter = MockAgentAdapter({"success_rate": 0.9})
    
    # Create orchestrator
    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=anthropic_client,
        workspace_path=Path("./workspace"),
    )
    
    # Run a task
    result = await orchestrator.run("Research quantum computing and write a report")
    ```

See urw_integration.py for detailed integration instructions.
"""

__version__ = "0.1.0"
__author__ = "Kevin Dragan / Claude"

# Core state management
from urw_state import (
    # Data classes
    Task,
    TaskStatus,
    Artifact,
    ArtifactType,
    CompletionConfidence,
    IterationResult,
    
    # State manager
    URWStateManager,
    GitCheckpointer,
)

# Task decomposition
from urw_decomposer import (
    # Decomposers
    Decomposer,
    TemplateDecomposer,
    LLMDecomposer,
    HybridDecomposer,
    
    # Plan management
    PlanManager,
    
    # Templates
    DECOMPOSITION_TEMPLATES,
    
    # Utilities
    estimate_task_complexity,
    validate_task_graph,
)

# Completion evaluation
from urw_evaluator import (
    # Result class
    EvaluationResult,
    
    # Evaluators
    Evaluator,
    BinaryCheckEvaluator,
    ConstraintEvaluator,
    LLMJudgeEvaluator,
    CompositeEvaluator,
    
    # Utilities
    create_default_evaluator,
    quick_evaluate,
)

# Orchestrator
from urw_orchestrator import (
    # Main orchestrator
    URWOrchestrator,
    OrchestratorStatus,
    
    # Configuration
    URWConfig,
    URWCallbacks,
    
    # Agent interface
    AgentLoopInterface,
    AgentExecutionResult,
    
    # Convenience functions
    run_universal_task,
    create_orchestrator_with_callbacks,
)

# Integration
from urw_integration import (
    # Adapters
    BaseAgentAdapter,
    ClaudeAgentSDKAdapter,
    UniversalAgentAdapter,
    MockAgentAdapter,
    
    # Factory
    create_adapter_for_system,
)

__all__ = [
    # Version
    "__version__",
    
    # State management
    "Task",
    "TaskStatus",
    "Artifact",
    "ArtifactType",
    "CompletionConfidence",
    "IterationResult",
    "URWStateManager",
    "GitCheckpointer",
    
    # Decomposition
    "Decomposer",
    "TemplateDecomposer",
    "LLMDecomposer",
    "HybridDecomposer",
    "PlanManager",
    "DECOMPOSITION_TEMPLATES",
    "estimate_task_complexity",
    "validate_task_graph",
    
    # Evaluation
    "EvaluationResult",
    "Evaluator",
    "BinaryCheckEvaluator",
    "ConstraintEvaluator",
    "LLMJudgeEvaluator",
    "CompositeEvaluator",
    "create_default_evaluator",
    "quick_evaluate",
    
    # Orchestrator
    "URWOrchestrator",
    "OrchestratorStatus",
    "URWConfig",
    "URWCallbacks",
    "AgentLoopInterface",
    "AgentExecutionResult",
    "run_universal_task",
    "create_orchestrator_with_callbacks",
    
    # Integration
    "BaseAgentAdapter",
    "ClaudeAgentSDKAdapter",
    "UniversalAgentAdapter",
    "MockAgentAdapter",
    "create_adapter_for_system",
]
