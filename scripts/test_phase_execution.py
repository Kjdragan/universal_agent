#!/usr/bin/env python3
"""
Test script for Phase-Based Execution in URWOrchestrator.
Uses MockAgentAdapter to simulate agent execution without LLM calls.
"""

import asyncio
import shutil
import sys
import os
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from universal_agent.urw import (
    URWConfig,
    URWOrchestrator,
    MockAgentAdapter,
    TaskStatus
)
from universal_agent.urw.decomposer import HybridDecomposer, Task

# Mock LLM Client for Decomposer/Evaluator
class MockLLMClient:
    def __init__(self):
        self.messages = MockMessages()

class MockMessages:
    def create(self, **kwargs):
        # Return a dummy response that won't crash parsing logic
        # For decomposer, we probably need a valid JSON
        return MockResponse()

class MockResponse:
    content = [type("obj", (object,), {"text": json.dumps({"tasks": []})})]

# We actually need a smarter mock or force the decomposer to return known tasks
# Or we can manually populate the state manager with tasks

async def test_phase_execution():
    workspace = Path("./test_phase_workspace")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir()
    
    print(f"Setting up test workspace: {workspace}")
    
    # Config
    config = URWConfig(
        max_iterations_per_task=3,
        max_total_iterations=10,
        verbose=True
    )
    
    # Components
    adapter = MockAgentAdapter({"success_rate": 1.0, "produce_artifacts": True})
    # We pass None for llm_client, hoping we can bypass LLM calls
    # But PhasePlanner might need it for complex cases. 
    # For simple cases (Pythonic), it doesn't need LLM if we force simple tasks.
    
    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=None, # Will use heuristics for PhasePlanner
        workspace_path=workspace,
        config=config
    )
    
    # Manually inject tasks into State Manager -> Database
    state = orchestrator.state_manager
    
    tasks = [
        Task(id="task_1", title="Research Topic A", description="Desc A", status=TaskStatus.PENDING),
        Task(id="task_2", title="Research Topic B", description="Desc B", status=TaskStatus.PENDING),
        Task(id="task_3", title="Synthesize A and B", description="Desc C", status=TaskStatus.PENDING, depends_on=["task_1", "task_2"]),
    ]
    
    print("Injecting 3 tasks (A, B -> C)...")
    state.create_tasks_batch(tasks)
    
    # Run Orchestrator Loop
    print("Starting Orchestrator Loop...")
    
    # We override _main_loop? No, we call run() but run() does decomposition.
    # We want to skip decomposition since we manually injected tasks.
    # We can call _main_loop direct?
    # run() calls: decompose -> confirm -> _main_loop
    
    # We'll just call _main_loop directly.
    orchestrator._should_stop = False
    orchestrator.status = "running"
    orchestrator._pause_event.set() # Unpause
    
    try:
        await orchestrator._main_loop()
    except Exception as e:
        print(f"Loop error: {e}")
        import traceback
        traceback.print_exc()

    # Verify Results
    print("\nVerifying State:")
    t1 = state.get_task("task_1")
    t2 = state.get_task("task_2")
    t3 = state.get_task("task_3")
    
    print(f"Task 1: {t1.status}")
    print(f"Task 2: {t2.status}")
    print(f"Task 3: {t3.status}")
    
    # Check iterations
    # We expect Task 1 and 2 to be in Phase 1 (parallel execution)
    # And Task 3 in Phase 2
    
    if t1.status == TaskStatus.COMPLETE and t2.status == TaskStatus.COMPLETE:
        print("✅ Phase 1 (Parallel tasks) successful")
    else:
        print("❌ Phase 1 failed")
        
    if t3.status == TaskStatus.COMPLETE:
        print("✅ Phase 2 (Dependent task) successful")

    # cleanup
    # shutil.rmtree(workspace)

import json
if __name__ == "__main__":
    asyncio.run(test_phase_execution())
