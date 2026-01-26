import asyncio
import os
import shutil
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "universal_agent" / "src"))

async def test_auto_interview():
    print("🧪 Starting Auto-Interview Verification...")
    
    # 1. Define command
    cmd = (
        "PYTHONPATH=src "
        "uv run python -m universal_agent.main "
        "--harness \"[TEST] Verify Auto Interview & History Reset\" "
        "--interview-auto \"1,1,1\" "  # Answers for dummy request
        "--urw-mock "                 # Use mock so we don't spend tokens
    )
    
    # Mocking is tricky because URW might not support mock fully yet in CLI
    # Actually, we want to test the INTERVIEW logic, which runs before URW/Harness execution proper.
    # But main.py calls run_harness.
    
    # Let's run a short real test (without mock) but kill it after planning?
    # Or use --harness-template? No, we want to test interview.
    
    # We'll rely on a manual run via run_command for safety,
    # but here is a script helper if needed.
    pass

if __name__ == "__main__":
    # Just print the command to run
    print("\nTo manually verify, run:")
    print("PYTHONPATH=src uv run python -m universal_agent.main "
          "--harness \"[TEST] Auto Interview\" "
          "--interview-auto \"1,1,1\"")
