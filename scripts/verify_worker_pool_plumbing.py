
import asyncio
import os
import sys
import logging
from pathlib import Path

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

# Clean up any previous test db
test_db = "test_pool.db"
if os.path.exists(test_db):
    os.remove(test_db)

# Configure logging
logging.basicConfig(level=logging.INFO)

from universal_agent.durable.worker_pool import WorkerPoolManager, PoolConfig, WorkerConfig, queue_run
from universal_agent.durable.db import connect_runtime_db

async def mock_run_handler(run_id: str, workspace_dir: str) -> bool:
    print(f"[{run_id}] Mock Handler: Starting work...")
    await asyncio.sleep(1) # Simulate work
    print(f"[{run_id}] Mock Handler: Work complete!")
    return True

async def verify_worker_pool():
    print("Locked & Loaded: Verifying Worker Pool Plumbing")
    
    # 1. Initialize DB
    conn = connect_runtime_db(test_db)
    # init_db(conn) - Handled by connect_runtime_db
    
    # 2. Config
    pool_config = PoolConfig(
        db_path=test_db,
        min_workers=2,
        max_workers=2,
        scale_up_threshold=1
    )
    
    # 3. Start Pool with Mock Handler
    pool = WorkerPoolManager(pool_config, run_handler=mock_run_handler)
    await pool.start()
    
    try:
        # 4. Queue Runs
        print("Queuing runs...")
        queue_run(conn, "run_1", "prompt 1")
        queue_run(conn, "run_2", "prompt 2")
        queue_run(conn, "run_3", "prompt 3")
        
        # 5. Wait for processing
        # We expect 3 runs to complete.
        max_wait = 10
        for _ in range(max_wait):
            stats = pool.get_pool_stats()
            print(f"Stats: {stats}")
            if stats["total_completed"] >= 3:
                print("SUCCESS: All runs completed.")
                break
            await asyncio.sleep(1)
        else:
            print("FAILURE: Timed out waiting for runs.")
            sys.exit(1)
            
    finally:
        await pool.stop()
        if os.path.exists(test_db):
            os.remove(test_db)

if __name__ == "__main__":
    asyncio.run(verify_worker_pool())
