#!/usr/bin/env python3
"""
URW Example Usage

This script demonstrates how to use URW with different configurations.
Run with MockAgentAdapter first to verify everything works, then
swap in your real agent adapter.

Usage:
    python examples.py --mode mock     # Test with mock adapter
    python examples.py --mode real     # Run with real adapter (after implementing)
"""

import asyncio
import argparse
from pathlib import Path
from datetime import datetime

# For real usage, you'd have:
# from anthropic import Anthropic

from urw_package import (
    # Core orchestrator
    URWOrchestrator,
    URWConfig,
    URWCallbacks,
    OrchestratorStatus,
    
    # Adapters
    MockAgentAdapter,
    BaseAgentAdapter,
    AgentExecutionResult,
    
    # State management
    URWStateManager,
    Task,
    TaskStatus,
    
    # Decomposition
    PlanManager,
    TemplateDecomposer,
    
    # Evaluation
    CompositeEvaluator,
    EvaluationResult,
)


# =============================================================================
# EXAMPLE 1: Basic Usage with Mock Adapter
# =============================================================================

async def example_basic_mock():
    """
    Basic example using MockAgentAdapter.
    Use this to test URW behavior before integrating your real agent.
    """
    print("\n" + "="*60)
    print("EXAMPLE 1: Basic Usage with Mock Adapter")
    print("="*60 + "\n")
    
    # Create mock adapter
    adapter = MockAgentAdapter({
        'success_rate': 0.9,       # 90% success rate
        'simulate_delay': 0.3,     # 300ms delay per iteration
        'produce_artifacts': True,  # Create mock output files
    })
    
    # For real usage, create Anthropic client:
    # client = Anthropic(api_key="your-key")
    # For mock, we'll create a minimal mock client
    class MockLLMClient:
        def __init__(self):
            self.messages = self
        def create(self, **kwargs):
            class Response:
                content = [type('obj', (object,), {'text': '[]'})()]
            return Response()
    
    client = MockLLMClient()
    
    # Create workspace
    workspace = Path("./example_workspace_basic")
    workspace.mkdir(exist_ok=True)
    
    # Create orchestrator with callbacks for visibility
    def on_progress(msg):
        print(f"  [Progress] {msg}")
    
    def on_task_complete(task, evaluation):
        print(f"  [Complete] {task.title} - Score: {evaluation.overall_score:.2f}")
    
    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=client,
        workspace_path=workspace,
        config=URWConfig(
            max_iterations_per_task=5,
            max_total_iterations=20,
            verbose=False,  # We're using callbacks instead
        ),
        callbacks=URWCallbacks(
            on_progress=on_progress,
            on_task_complete=on_task_complete,
        ),
    )
    
    # Run a simple request
    result = await orchestrator.run(
        "Write a blog post about artificial intelligence trends"
    )
    
    print(f"\nResult:")
    print(f"  Status: {result['status']}")
    print(f"  Iterations: {result['total_iterations']}")
    print(f"  Tasks: {result['task_stats']}")
    print(f"  Complete: {result['is_complete']}")
    
    return result


# =============================================================================
# EXAMPLE 2: State Manager Direct Usage
# =============================================================================

def example_state_manager():
    """
    Example of using URWStateManager directly for state inspection.
    Useful for debugging and understanding the state structure.
    """
    print("\n" + "="*60)
    print("EXAMPLE 2: State Manager Direct Usage")
    print("="*60 + "\n")
    
    workspace = Path("./example_workspace_state")
    workspace.mkdir(exist_ok=True)
    
    # Initialize state manager
    state = URWStateManager(workspace)
    
    # Store original request
    state.set_original_request("Research quantum computing")
    
    # Create tasks manually
    task1 = Task(
        id="task_001",
        title="Gather research sources",
        description="Find authoritative sources on quantum computing",
        depends_on=[],
        verification_type="composite",
        binary_checks=["file_exists:sources.md"],
        constraints=[{"type": "min_length", "value": 500}],
    )
    
    task2 = Task(
        id="task_002", 
        title="Write summary",
        description="Summarize the key findings",
        depends_on=["task_001"],
        verification_type="qualitative",
        evaluation_rubric="Is the summary comprehensive and accurate?",
    )
    
    state.create_task(task1)
    state.create_task(task2)
    
    print("Tasks created:")
    for task in state.get_all_tasks():
        print(f"  - {task.id}: {task.title} ({task.status.value})")
    
    # Get next executable task
    next_task = state.get_next_task()
    print(f"\nNext executable task: {next_task.title if next_task else 'None'}")
    
    # Simulate task execution
    print("\nSimulating task execution...")
    state.update_task_status("task_001", TaskStatus.IN_PROGRESS, iteration=1)
    
    # Generate context
    context = state.generate_agent_context(task1)
    print(f"\nGenerated context preview:\n{context[:500]}...")
    
    # Complete task
    state.update_task_status("task_001", TaskStatus.COMPLETE, iteration=1)
    
    # Record a failed approach
    state.record_failed_approach(
        approach="Using outdated Wikipedia articles",
        why_failed="Information from 2020, not current enough",
        task_id="task_001",
        iteration=1
    )
    
    # Check next task
    next_task = state.get_next_task()
    print(f"\nAfter task_001 complete, next task: {next_task.title if next_task else 'None'}")
    
    # Show guardrails
    failed = state.get_failed_approaches()
    print(f"\nFailed approaches recorded: {len(failed)}")
    for f in failed:
        print(f"  - {f['approach']}: {f['why_failed']}")
    
    # Check state files
    print(f"\nState files in {workspace / '.urw'}:")
    for f in (workspace / '.urw').iterdir():
        if f.is_file():
            print(f"  - {f.name}")
    
    state.close()
    return state


# =============================================================================
# EXAMPLE 3: Custom Decomposition
# =============================================================================

def example_custom_decomposition():
    """
    Example of using the decomposer directly.
    Useful for testing how requests get broken down.
    """
    print("\n" + "="*60)
    print("EXAMPLE 3: Custom Decomposition")
    print("="*60 + "\n")
    
    # Use template decomposer (no LLM needed)
    decomposer = TemplateDecomposer()
    
    # Test various requests
    requests = [
        "Research the history of AI and write a comprehensive report",
        "Send an email outreach campaign to potential partners",
        "Analyze this document and extract key insights",
        "Create a blog post about machine learning",
    ]
    
    for request in requests:
        can_handle = decomposer.can_handle(request)
        print(f"\nRequest: {request[:50]}...")
        print(f"  Template match: {can_handle}")
        
        if can_handle:
            tasks = decomposer.decompose(request)
            print(f"  Tasks generated: {len(tasks)}")
            for task in tasks:
                deps = f" (depends on: {task.depends_on})" if task.depends_on else ""
                print(f"    - {task.title}{deps}")


# =============================================================================
# EXAMPLE 4: Evaluation Strategies
# =============================================================================

def example_evaluation():
    """
    Example of different evaluation strategies.
    Shows how binary, constraint, and composite evaluation work.
    """
    print("\n" + "="*60)
    print("EXAMPLE 4: Evaluation Strategies")
    print("="*60 + "\n")
    
    from urw_package import (
        BinaryCheckEvaluator,
        ConstraintEvaluator,
    )
    
    workspace = Path("./example_workspace_eval")
    workspace.mkdir(exist_ok=True)
    artifacts_dir = workspace / '.urw' / 'artifacts'
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a test file
    test_file = artifacts_dir / "test_output.md"
    test_file.write_text("""# Test Report

This is a test report with some content.

## Section 1
Here is some analysis about the topic.

## Section 2  
Here are the conclusions and recommendations.
""")
    
    # Test binary check evaluation
    print("Binary Check Evaluation:")
    binary_eval = BinaryCheckEvaluator()
    
    task_with_binary = Task(
        id="test",
        title="Test",
        description="Test",
        binary_checks=["file_exists:test_output.md", "file_exists:missing.md"],
    )
    
    result = binary_eval.evaluate(task_with_binary, [], "", workspace)
    print(f"  Is complete: {result.is_complete}")
    print(f"  Score: {result.overall_score:.2f}")
    print(f"  Binary results: {result.binary_results}")
    print(f"  Missing: {result.missing_elements}")
    
    # Test constraint evaluation
    print("\nConstraint Evaluation:")
    constraint_eval = ConstraintEvaluator()
    
    from urw_package import Artifact, ArtifactType
    
    artifact = Artifact(
        id="art_1",
        task_id="test",
        artifact_type=ArtifactType.FILE,
        file_path="test_output.md",
    )
    
    task_with_constraints = Task(
        id="test",
        title="Test",
        description="Test",
        constraints=[
            {"type": "min_length", "value": 100},
            {"type": "contains", "value": "conclusions"},
            {"type": "min_length", "value": 10000},  # Will fail
        ],
    )
    
    result = constraint_eval.evaluate(task_with_constraints, [artifact], "", workspace)
    print(f"  Is complete: {result.is_complete}")
    print(f"  Score: {result.overall_score:.2f}")
    print(f"  Constraint results:")
    for k, v in result.constraint_results.items():
        status = "✓" if v[0] else "✗"
        print(f"    {status} {k}: {v[1] or 'passed'}")


# =============================================================================
# EXAMPLE 5: Resume from Checkpoint
# =============================================================================

async def example_checkpoint_resume():
    """
    Example of checkpointing and resuming.
    Shows how to resume work after interruption.
    """
    print("\n" + "="*60)
    print("EXAMPLE 5: Checkpoint and Resume")
    print("="*60 + "\n")
    
    workspace = Path("./example_workspace_checkpoint")
    workspace.mkdir(exist_ok=True)
    
    # Initialize state
    state = URWStateManager(workspace)
    
    # Create some tasks
    state.set_original_request("Test checkpoint/resume")
    
    for i in range(3):
        task = Task(
            id=f"task_{i:03d}",
            title=f"Task {i+1}",
            description=f"Description for task {i+1}",
        )
        state.create_task(task)
    
    # Simulate some iterations
    iteration = state.start_iteration("task_000")
    print(f"Started iteration {iteration}")
    
    from urw_package import IterationResult, CompletionConfidence
    
    result = IterationResult(
        iteration=iteration,
        task_id="task_000",
        outcome="success",
        completion_confidence=CompletionConfidence.HIGH,
        context_tokens_used=1000,
        tools_invoked=["tool_1", "tool_2"],
        learnings=["Learned something useful"],
        artifacts_produced=[],
        failed_approaches=[],
        agent_output="Task completed successfully",
    )
    
    commit_sha = state.complete_iteration(result)
    print(f"Checkpoint created: {commit_sha[:8]}...")
    
    # Show checkpoint history
    history = state.checkpointer.get_checkpoint_history(5)
    print(f"\nCheckpoint history:")
    for cp in history:
        print(f"  {cp['sha'][:8]}: {cp['message']}")
    
    # Simulate rollback
    if len(history) > 1:
        print(f"\nCould rollback to: {history[-1]['sha'][:8]}")
        print("  (Not actually rolling back in this example)")
    
    state.close()


# =============================================================================
# EXAMPLE 6: Full Integration Template
# =============================================================================

async def example_full_integration():
    """
    Full integration example template.
    This is the structure you'd use for real integration.
    """
    print("\n" + "="*60)
    print("EXAMPLE 6: Full Integration Template")
    print("="*60 + "\n")
    
    print("""
This example shows the complete integration structure.
To use with your real agent system:

1. Copy the UniversalAgentURWAdapter class
2. Implement _create_agent() with your agent creation code
3. Implement _run_agent() with your agent execution code
4. Extract artifacts, side effects, learnings from results

Example adapter structure:

```python
class YourAgentAdapter(BaseAgentAdapter):
    async def _create_agent(self):
        # Import your agent
        from your_package import Agent
        
        # Create FRESH instance
        return Agent(
            model=self.config['model'],
            api_key=self.config['api_key'],
        )
    
    async def _run_agent(self, agent, prompt, workspace_path):
        # Run your agent
        result = await agent.run(prompt)
        
        # Extract and return structured result
        return AgentExecutionResult(
            success=result.success,
            output=result.final_response,
            artifacts_produced=self._extract_artifacts(result),
            side_effects=self._extract_side_effects(result),
            learnings=self._extract_learnings(result),
            failed_approaches=self._extract_failures(result),
            tools_invoked=[t.name for t in result.tool_calls],
            context_tokens_used=result.tokens_used,
        )
```
""")


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(description="URW Examples")
    parser.add_argument(
        '--example', 
        type=int, 
        choices=[1, 2, 3, 4, 5, 6],
        help="Which example to run (1-6)"
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help="Run all examples"
    )
    
    args = parser.parse_args()
    
    if args.all or args.example is None:
        examples = [1, 2, 3, 4, 5, 6]
    else:
        examples = [args.example]
    
    for ex in examples:
        if ex == 1:
            await example_basic_mock()
        elif ex == 2:
            example_state_manager()
        elif ex == 3:
            example_custom_decomposition()
        elif ex == 4:
            example_evaluation()
        elif ex == 5:
            await example_checkpoint_resume()
        elif ex == 6:
            await example_full_integration()
    
    print("\n" + "="*60)
    print("Examples complete!")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
