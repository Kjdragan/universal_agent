
import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
import re
import time

# Set up environment
WORKSPACE_ROOT = os.path.abspath("AGENT_RUN_WORKSPACES")
TEST_RUN_ID = f"test_recovery_{uuid.uuid4().hex[:8]}"
TEST_WORKSPACE = os.path.join(WORKSPACE_ROOT, f"session_{TEST_RUN_ID}")
DB_PATH = os.path.join(WORKSPACE_ROOT, "runtime_state.db")

from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.db import connect_runtime_db

def setup_db():
   conn = connect_runtime_db(DB_PATH)
   ensure_schema(conn)
   conn.close()


def run_recovery_test():
    print(f"🧪 Starting Report Recovery Harness Test")
    print(f"👉 Run ID: {TEST_RUN_ID}")
    
    # 1. Clean previous test
    if os.path.exists(TEST_WORKSPACE):
        shutil.rmtree(TEST_WORKSPACE)
    os.makedirs(TEST_WORKSPACE, exist_ok=True)
    
    # 2. Define Mission
    # The goal is to provoke a Write error and verify the harness/agent recovers
    # and eventually produces the correct output.
    mission_prompt = (
        "Mission:\n"
        "1. Attempt to write a file 'crash_test.txt' with EMPTY content (0 bytes). \n"
        "   (This is expected to fail with InputValidationError)\n"
        "2. If/When that fails, catch the error and write a new file 'recovery_success.txt' with content 'Recovered'.\n"
        "3. Output 'TASK_COMPLETE' only when 'recovery_success.txt' exists.\n"
    )

    cmd = [
        "uv", "run", "python", "-m", "universal_agent.main",
        "--run-id", TEST_RUN_ID,
        "--workspace", TEST_WORKSPACE,
        "--max-iterations", "10",
        "--completion-promise", "TASK_COMPLETE",
        "--harness", "report_recovery_test" # Activate harness mode
    ]
    
    env = os.environ.copy()
    env["PYTHONPATH"] = "src" 
    env["UA_RUNTIME_DB_PATH"] = DB_PATH

    print(f"🚀 Launching Agent (Timeout: 180s)...")
    start_time = time.time()
    
    try:
        process = subprocess.run(
            cmd,
            input=mission_prompt.encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=180 # 3 minutes max
        )
    except subprocess.TimeoutExpired:
        print("❌ TIMEOUT: Agent failed to recover in 180s")
        return False

    duration = time.time() - start_time
    output = process.stdout.decode("utf-8")
    err_output = process.stderr.decode("utf-8")
    
    print(f"⏱️ Duration: {duration:.1f}s")
    
    # 3. Analyze Results
    print("\n=== Verification ===")
    
    recovery_file = os.path.join(TEST_WORKSPACE, "recovery_success.txt")
    if os.path.exists(recovery_file):
        print("✅ Recovery file 'recovery_success.txt' created.")
    else:
        print("❌ Recovery file MISSING. Agent did not recover.")
        print("--- Last 1KB of Output ---")
        print(output[-1000:])
        return False

    if "InputValidationError" in output or "InputValidationError" in err_output:
        print("✅ Confirmed: InputValidationError was triggered (Simulating the 0-byte write crash).")
    elif "tool_use_error" in output:
        print("✅ Confirmed: Tool error triggered.")
    else:
        print("⚠️ Warning: Did not see explicit Validation Error in logs. Did it fail differently?")

    # Check for Harness completion
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT completion_promise, run_status FROM runs WHERE run_id = ?", (TEST_RUN_ID,)).fetchone()
    
    if row and row['completion_promise'] == "TASK_COMPLETE":
        print("✅ DB Record: Completion Promise met.")
    else:
        print(f"❌ DB Record: Promise mismatch or missing. Status: {row['run_status'] if row else 'None'}")
        return False

    print("\n🎉 TEST PASSED: Agent recovered from tool error and completed task.")
    return True

if __name__ == "__main__":
    setup_db()
    success = run_recovery_test()
    sys.exit(0 if success else 1)
