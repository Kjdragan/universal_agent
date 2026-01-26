
import asyncio
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# Add src to path
sys.path.append("/home/kjdragan/lrepos/universal_agent/src")

load_dotenv()

from universal_agent.urw.evaluator import CompositeEvaluator, EvaluationResult
from universal_agent.urw.state import Task, Artifact, ArtifactType
from universal_agent.urw.harness_orchestrator import HarnessOrchestrator
from claude_agent_sdk.client import ClaudeSDKClient

async def debug_verification():
    # 1. Setup paths
    session_path = Path("/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/harness_20260126_010154/session_phase_1")
    plan_path = Path("/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/harness_20260126_010154/plan_e410ab6e-0d70-408d-94f4-22c557807b9f.json")
    
    if not session_path.exists():
        print(f"Session path not found: {session_path}")
        return

    # 2. Load Plan Tasks
    with open(plan_path) as f:
        plan_data = json.load(f)
    
    # Extract tasks from Phase 0 (Research & Report)
    phase_tasks = plan_data["phases"][0]["tasks"]
    print(f"Found {len(phase_tasks)} tasks to verify.")

    # 3. Scan Artifacts
    # Mimic HarnessOrchestrator._scan_session_artifacts logic
    artifacts = []
    work_products = session_path / "work_products"
    
    if work_products.exists():
        for f in work_products.rglob("*"):
             if f.is_file():
                 rel_path = str(f.relative_to(session_path))
                 artifacts.append(Artifact(
                     id=rel_path,
                     task_id="unknown",
                     artifact_type=ArtifactType.FILE,
                     file_path=rel_path
                 ))
                 print(f"  Found Artifact: {rel_path}")

    # 4. Run Evaluation
    # We need a dummy client or real one. Let's try to mock the LLM client or use a real one if env vars exist.
    # We'll use a simple mock for now to test logical constraints first, then maybe LLM.
    
    class MockLLMClient:
        pass # The evaluator tries to unwrap this, it will fail and try fallback.
             # We want to see if fallback works or if we need to fix it.
    
    # Check if we have API key for fallback
    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
    print(f"API Key present: {bool(api_key)}")
    
    evaluator = CompositeEvaluator(
        llm_client=MockLLMClient(),
        model="claude-3-5-sonnet-20241022" # Use valid model
    )

    print("\n--- Starting Verification ---\n")
    
    # Needs adapter to convert schema
    from universal_agent.urw.adapter import HarnessAdapter
    from universal_agent.urw.plan_schema import AtomicTask

    for task_data in phase_tasks:
        # 1. Create AtomicTask (Plan Schema)
        atomic = AtomicTask(
            id=task_data["id"],
            name=task_data["name"],
            description=task_data["description"],
            success_criteria=task_data.get("success_criteria", []),
            use_case=task_data.get("use_case", "")
        )
        
        # 2. Adapt to State Task (Evaluator Schema)
        task = HarnessAdapter.atomic_task_to_state_task(atomic)
        
        # Add manual binary check for the report task to test binary logic specifically
        if "HTML Report" in task.title:
             task.binary_checks.append("file_exists:work_products/apple_samsung_esg_comparison_report.html")

        try:
            # We want to test the FULL composite evaluator, not just qualitative
            # This tests if binary checks work and if qualitative falls back gracefully
            result = evaluator.evaluate(
                task, artifacts, "Agent Output Placeholder", session_path
            )
            print(f"Task '{task.title}': Success={result.is_complete}, Score={result.overall_score}")

            if not result.is_complete:
                 print(f"  Missing: {result.missing_elements}")
                 print(f"  Reasoning: {result.qualitative_reasoning}")
        except Exception as e:
            print(f"❌ Verification FAILED for '{task.title}': {e}")

if __name__ == "__main__":
    asyncio.run(debug_verification())
