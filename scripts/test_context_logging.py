import asyncio
import json
from pathlib import Path
import sys

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT / "src"))

from universal_agent.tools.context_logging import (
    GAPS_FILE,
    TASKS_FILE,
    get_pending_gaps,
    log_context_gap,
    log_offline_task,
)


async def test_logging():
    print("🚀 Testing Context Gap Logging...")
    
    # 1. Clear existing files for clean test (optional, but good for reliable test)
    # Actually, let's just append and check for our specific unique gap
    
    # 2. Test logging a gap
    unique_q = f"What is your favorite color? (Test {id(object())})"
    await log_context_gap.handler({
        "question": unique_q,
        "category": "profile_context",
        "urgency": "deferred",
        "context_source": "test_script"
    })
    
    # 3. Test retrieving gaps
    gaps = get_pending_gaps()
    print(f"Pending gaps count: {len(gaps)}")
    found_gap = next((g for g in gaps if g["question"] == unique_q), None)
    
    if found_gap:
        print(f"✅ Gap correctly persisted: {found_gap['id']}")
    else:
        print("❌ Gap NOT found in storage.")
        
    # 4. Test logging offline task
    task_desc = f"Research color theory (Test {id(object())})"
    log_offline_task(task_desc, "test_script_run")
    
    # Verify task file
    if TASKS_FILE.exists():
        with open(TASKS_FILE, "r") as f:
            tasks = json.load(f)
        found_task = next((t for t in tasks if t["description"] == task_desc), None)
        if found_task:
             print(f"✅ Offline task persisted: {found_task['id']}")
        else:
             print("❌ Task NOT found in storage.")
    else:
        print("❌ Tasks file not created.")

if __name__ == "__main__":
    asyncio.run(test_logging())
