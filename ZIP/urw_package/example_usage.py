"""
Example: Running URW with the Mock Adapter

This demonstrates the full URW flow without requiring your actual
multi-agent system. Use this to understand how URW works before
integrating with your real system.
"""

import asyncio
from pathlib import Path
from datetime import datetime

# Mock the Anthropic client for testing without API key
class MockAnthropicClient:
    """Mock Anthropic client for testing."""
    
    class MockContent:
        def __init__(self, text):
            self.text = text
    
    class MockResponse:
        def __init__(self, text):
            self.content = [MockAnthropicClient.MockContent(text)]
    
    class Messages:
        def create(self, model, max_tokens, messages):
            # Return a mock decomposition response
            user_msg = messages[0]['content'] if messages else ""
            
            if "decompose" in user_msg.lower() or "break down" in user_msg.lower():
                return MockAnthropicClient.MockResponse('''[
    {
        "id": "task_001",
        "title": "Research Phase",
        "description": "Gather information from sources",
        "depends_on": [],
        "verification_type": "composite",
        "binary_checks": ["file_exists:research_notes.md"],
        "max_iterations": 5
    },
    {
        "id": "task_002", 
        "title": "Analysis Phase",
        "description": "Analyze gathered information",
        "depends_on": ["task_001"],
        "verification_type": "qualitative",
        "evaluation_rubric": "Is the analysis comprehensive?",
        "max_iterations": 5
    },
    {
        "id": "task_003",
        "title": "Report Writing",
        "description": "Write final report",
        "depends_on": ["task_002"],
        "verification_type": "composite",
        "binary_checks": ["file_exists:final_report.md"],
        "constraints": [{"type": "min_length", "value": 500}],
        "max_iterations": 5
    }
]''')
            elif "evaluate" in user_msg.lower():
                return MockAnthropicClient.MockResponse('''{
    "overall_score": 0.85,
    "is_complete": true,
    "reasoning": "Task appears complete based on artifacts",
    "missing_elements": [],
    "suggested_actions": [],
    "strengths": ["Good coverage", "Clear structure"]
}''')
            else:
                return MockAnthropicClient.MockResponse("Mock response")
    
    def __init__(self, api_key=None):
        self.messages = self.Messages()


async def run_example():
    """Run a complete URW example with mock components."""
    
    # Import URW components
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    
    from urw_state import URWStateManager, Task, TaskStatus, Artifact, ArtifactType
    from urw_decomposer import PlanManager, HybridDecomposer
    from urw_evaluator import CompositeEvaluator
    from urw_orchestrator import (
        URWOrchestrator, URWConfig, URWCallbacks,
        AgentExecutionResult
    )
    from urw_integration import MockAgentAdapter
    
    print("="*70)
    print("URW EXAMPLE: Full Execution Flow")
    print("="*70)
    print()
    
    # Setup
    workspace = Path("./example_workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    
    # Create mock adapter (simulates your agent system)
    adapter = MockAgentAdapter({
        "success_rate": 0.95,      # 95% success rate
        "simulate_delay": 0.2,     # 200ms delay per iteration
        "produce_artifacts": True,  # Create mock artifacts
    })
    
    # Create mock LLM client
    client = MockAnthropicClient()
    
    # Define callbacks for visibility
    def on_progress(msg):
        print(f"  📝 {msg}")
    
    def on_task_start(task, iteration):
        print(f"\n  🚀 Starting: {task.title} (iteration {iteration})")
    
    def on_task_complete(task, evaluation):
        print(f"  ✅ Completed: {task.title} (score: {evaluation.overall_score:.2f})")
    
    def on_task_failed(task, reason):
        print(f"  ❌ Failed: {task.title} - {reason}")
    
    callbacks = URWCallbacks(
        on_progress=on_progress,
        on_task_start=on_task_start,
        on_task_complete=on_task_complete,
        on_task_failed=on_task_failed,
    )
    
    # Create orchestrator
    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=client,
        workspace_path=workspace,
        config=URWConfig(
            max_iterations_per_task=10,
            max_total_iterations=50,
            enable_dynamic_replanning=True,
            verbose=False,  # We're using callbacks instead
        ),
        callbacks=callbacks,
    )
    
    # Run a task
    print("📋 Request: Research AI safety and write a report")
    print("-"*70)
    
    result = await orchestrator.run(
        "Research recent developments in AI safety and write a comprehensive report"
    )
    
    # Display results
    print("\n" + "="*70)
    print("EXECUTION SUMMARY")
    print("="*70)
    print(f"  Status:           {result['status']}")
    print(f"  Tasks Completed:  {result['tasks_completed']}")
    print(f"  Tasks Failed:     {result['tasks_failed']}")
    print(f"  Total Iterations: {result['total_iterations']}")
    print(f"  Plan Complete:    {result['is_complete']}")
    print(f"  Workspace:        {result['workspace_path']}")
    print(f"  Artifacts Dir:    {result['artifacts_dir']}")
    print(f"  Final Checkpoint: {result['final_checkpoint'][:12]}...")
    
    # Show state files
    print("\n" + "-"*70)
    print("STATE FILES CREATED:")
    print("-"*70)
    
    urw_dir = workspace / '.urw'
    
    # Progress file
    progress_file = urw_dir / 'progress.md'
    if progress_file.exists():
        print("\n📄 progress.md (excerpt):")
        content = progress_file.read_text()
        for line in content.split('\n')[:15]:
            print(f"  {line}")
        print("  ...")
    
    # Guardrails file
    guardrails_file = urw_dir / 'guardrails.md'
    if guardrails_file.exists():
        print("\n📄 guardrails.md (excerpt):")
        content = guardrails_file.read_text()
        for line in content.split('\n')[:10]:
            print(f"  {line}")
    
    # Task plan
    task_plan = urw_dir / 'task_plan.json'
    if task_plan.exists():
        print("\n📄 task_plan.json (excerpt):")
        content = task_plan.read_text()
        print(f"  {content[:500]}...")
    
    # Artifacts
    artifacts_dir = urw_dir / 'artifacts'
    if artifacts_dir.exists():
        print("\n📁 Artifacts created:")
        for f in artifacts_dir.glob('**/*'):
            if f.is_file():
                print(f"  - {f.relative_to(artifacts_dir)}")
    
    # Git history
    print("\n📚 Git checkpoints:")
    import subprocess
    git_log = subprocess.run(
        ['git', 'log', '--oneline', '-5'],
        cwd=workspace,
        capture_output=True,
        text=True
    )
    for line in git_log.stdout.strip().split('\n'):
        if line:
            print(f"  {line}")
    
    print("\n" + "="*70)
    print("Example complete! Explore the workspace at:", workspace)
    print("="*70)


async def demonstrate_state_manager():
    """Demonstrate the state manager independently."""
    
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    
    from urw_state import (
        URWStateManager, Task, TaskStatus, 
        Artifact, ArtifactType, IterationResult,
        CompletionConfidence
    )
    
    print("\n" + "="*70)
    print("STATE MANAGER DEMONSTRATION")
    print("="*70)
    
    workspace = Path("./state_demo")
    workspace.mkdir(parents=True, exist_ok=True)
    
    # Create state manager
    sm = URWStateManager(workspace)
    
    # Store original request
    sm.set_original_request("Demo request for state management")
    
    # Create tasks
    print("\n1. Creating tasks...")
    task1 = Task(
        id="demo_001",
        title="First Task",
        description="Do the first thing",
        binary_checks=["file_exists:output1.txt"],
    )
    task2 = Task(
        id="demo_002",
        title="Second Task",
        description="Do the second thing",
        depends_on=["demo_001"],
        evaluation_rubric="Is it good?",
    )
    
    sm.create_task(task1)
    sm.create_task(task2)
    print(f"   Created {len(sm.get_all_tasks())} tasks")
    
    # Get next task
    print("\n2. Getting next executable task...")
    next_task = sm.get_next_task()
    print(f"   Next task: {next_task.title if next_task else 'None'}")
    
    # Start iteration
    print("\n3. Starting iteration...")
    iteration = sm.start_iteration(task1.id)
    print(f"   Started iteration {iteration}")
    
    # Update task status
    sm.update_task_status(task1.id, TaskStatus.IN_PROGRESS, iteration)
    
    # Register artifact
    print("\n4. Registering artifact...")
    artifact = Artifact(
        id="art_001",
        task_id=task1.id,
        artifact_type=ArtifactType.FILE,
        file_path="output1.txt",
    )
    sm.register_artifact(artifact, content="Hello from artifact!")
    print(f"   Registered: {artifact.file_path}")
    
    # Record side effect
    print("\n5. Recording side effect...")
    executed = sm.record_side_effect(
        task_id=task1.id,
        effect_type="api_call",
        idempotency_key="api_call_123",
        details={"endpoint": "/api/demo"},
        iteration=iteration
    )
    print(f"   Side effect recorded: {executed}")
    
    # Try to record same side effect again
    executed_again = sm.record_side_effect(
        task_id=task1.id,
        effect_type="api_call",
        idempotency_key="api_call_123",  # Same key
        details={"endpoint": "/api/demo"},
        iteration=iteration
    )
    print(f"   Same side effect again: {executed_again} (should be False)")
    
    # Record failed approach
    print("\n6. Recording failed approach...")
    sm.record_failed_approach(
        approach="Direct scraping",
        why_failed="Rate limited",
        task_id=task1.id,
        iteration=iteration
    )
    print("   Recorded guardrail")
    
    # Complete iteration
    print("\n7. Completing iteration...")
    result = IterationResult(
        iteration=iteration,
        task_id=task1.id,
        outcome="success",
        completion_confidence=CompletionConfidence.HIGH,
        context_tokens_used=1500,
        tools_invoked=["web_search", "file_write"],
        learnings=["APIs are rate limited", "Need to cache results"],
        artifacts_produced=["art_001"],
        failed_approaches=[{"approach": "Direct scraping", "why_failed": "Rate limited"}],
        agent_output="Task completed successfully"
    )
    commit_sha = sm.complete_iteration(result)
    print(f"   Checkpoint created: {commit_sha[:12]}...")
    
    # Mark task complete
    sm.update_task_status(task1.id, TaskStatus.COMPLETE, iteration)
    
    # Generate context for next task
    print("\n8. Generating context for next task...")
    next_task = sm.get_next_task()
    if next_task:
        context = sm.generate_agent_context(next_task)
        print("   Context preview:")
        for line in context.split('\n')[:15]:
            print(f"   {line}")
        print("   ...")
    
    # Show stats
    print("\n9. Plan statistics:")
    stats = sm.get_completion_stats()
    for status, count in stats.items():
        print(f"   {status}: {count}")
    
    sm.close()
    print(f"\nState files at: {workspace / '.urw'}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "state":
        asyncio.run(demonstrate_state_manager())
    else:
        asyncio.run(run_example())
        print("\nRun with 'python example_usage.py state' to see state manager demo")
