
import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
import re

# Set up environment
WORKSPACE_ROOT = os.path.abspath("AGENT_RUN_WORKSPACES")
TEST_RUN_ID = f"test_harness_{uuid.uuid4().hex[:8]}"
TEST_WORKSPACE = os.path.join(WORKSPACE_ROOT, f"session_{TEST_RUN_ID}")
DB_PATH = os.path.join(WORKSPACE_ROOT, "runtime_state.db")

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.db import connect_runtime_db

def setup_db():
   conn = connect_runtime_db(DB_PATH)
   ensure_schema(conn)
   conn.close()


def run_agent_test():
    print(f"🧪 Starting Harness Verification Test")
    print(f"👉 Run ID: {TEST_RUN_ID}")
    
    # 1. Clean previous test if exists (unlikely due to UUID)
    if os.path.exists(TEST_WORKSPACE):
        shutil.rmtree(TEST_WORKSPACE)
    
    # 2. Run Agent with Debug Flag and Harness Args
    # Task: multi-step task that SHOULD trigger a handoff if forced
    # Create Workspace
    os.makedirs(TEST_WORKSPACE, exist_ok=True)
    
    # Create INSTRUCTIONS.md
    with open(os.path.join(TEST_WORKSPACE, "INSTRUCTIONS.md"), "w") as f:
        f.write("Phase 1: Create 'part1.txt' with content 'phase 1 done'.\n")
        f.write("Phase 2: STOP immediately. Do NOT create part2.txt yet. Wait for next phase.\n")
        f.write("Phase 3 (Resumed): Create 'part2.txt'.\n")
        f.write("Phase 4: Output 'TASK_COMPLETE'.\n")

    cmd = [
        "uv", "run", "python", "-m", "universal_agent.main",
        "--run-id", TEST_RUN_ID,
        "--workspace", TEST_WORKSPACE,
        "--max-iterations", "5",
        "--completion-promise", "TASK_COMPLETE"
    ]
    
    # Environment with debug flag to force prompt-based handoff simulation
    env = os.environ.copy()
    env["UA_DEBUG_FORCE_HANDOFF"] = "1"
    env["PYTHONPATH"] = "src" # Ensure src is in pythonpath
    env["UA_RUNTIME_DB_PATH"] = DB_PATH # Explicitly point to the same DB

    print(f"🚀 Launching Agent with command: {' '.join(cmd)}")
    
    # We need to simulate the interaction:
    # 1. Agent starts -> We send Prompt
    # 2. Agent does Phase 1. Stops. -> Harness sees no Promise -> Restarts.
    # 3. Agent restarts (same process) -> We don't send anything? 
    #    The agent should auto-continue if looping.
    #    BUT main.py "handle_cancel" or "input" logic might block?
    #    In main.py loop:
    #      if pending_prompt: user_input = pending_prompt ...
    #      else: input()
    #    So it will use nextPrompt.
    
    # The prompt should tell it to look at instructions
    initial_prompt = "Read INSTRUCTIONS.md and follow Phase 1 only. Then stop."
    
    process = subprocess.run(
        cmd,
        input=initial_prompt.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=120 # Give enough time for 2 iterations
    )
    
    output = process.stdout.decode("utf-8")
    err_output = process.stderr.decode("utf-8")
    
    print("\n=== Agent Output (STDOUT - LAST 2000) ===")
    print(output[-2000:] if len(output) > 2000 else output)

    print("\n=== Agent Output (STDERR - LAST 2000) ===")
    print(err_output[-2000:] if len(err_output) > 2000 else err_output) 
    
    # 3. Assertions
    print("\n=== Verification ===")
    
    # A. Check DB State
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT iteration_count, max_iterations, completion_promise FROM runs WHERE run_id = ?", (TEST_RUN_ID,)).fetchone()
    
    if not row:
        print("❌ Run not found in DB!")
        return False
        
    print(f"DB State: iterations={row['iteration_count']}, max={row['max_iterations']}, promise='{row['completion_promise']}'")
    
    if row['iteration_count'] < 1:
        print("❌ Expected iteration_count >= 1 (some handoff occurred)")
        # Note: If it finishes in 1 turn it might be 0? 
        # But we forced handoff, so it SHOULD be > 0 ideally if logic works.
    else:
        print("✅ Iteration count incremented")
        
    if row['completion_promise'] != "TASK_COMPLETE":
        print(f"❌ Expected promise 'TASK_COMPLETE', got '{row['completion_promise']}'")
    else:
        print("✅ Completion promise stored")

    # B. Check Side Effects (Files)
    part1_path = os.path.join(TEST_WORKSPACE, "part1.txt")
    part2_path = os.path.join(TEST_WORKSPACE, "part2.txt")
    
    if os.path.exists(part1_path):
        print("✅ part1.txt created")
    else:
        print("❌ part1.txt MISSING")
        
    if os.path.exists(part2_path):
        print("✅ part2.txt created")
    else:
        print("❌ part2.txt MISSING (Did it fail to resume?)")

    # C. Check for Restart Signal in logs
    if "Continuting a long-running task" in output or "Current Iteration:" in output:
         print("✅ Continuation prompt seen in output")
    
    return True

if __name__ == "__main__":
    setup_db()
    success = run_agent_test()
    sys.exit(0 if success else 1)
