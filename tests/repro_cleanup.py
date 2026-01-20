
import asyncio
import os
import json
from pathlib import Path
# We need to install the package in editable mode or set PYTHONPATH
from universal_agent.scripts.cleanup_report import cleanup_report_async

TEST_WORKSPACE = Path("/tmp/test_workspace_cleanup_verif")

def setup_test_env():
    if TEST_WORKSPACE.exists():
        import shutil
        shutil.rmtree(TEST_WORKSPACE)
    
    TEST_WORKSPACE.mkdir(parents=True)
    sections_dir = TEST_WORKSPACE / "work_products" / "_working" / "sections"
    sections_dir.mkdir(parents=True)
    
    # Create dummy sections with some duplicate stats and placeholders
    (sections_dir / "01_intro.md").write_text(
        "# Introduction\n\nCivilian casualties rose by 31% in 2025. [INSERT SOURCE]\n"
    )
    (sections_dir / "02_stats.md").write_text(
        "## Statistics\n\nCivilian casualties rose by 31% in 2025. As mentioned in the intro, this is bad.\nTODO: Add more numbers."
    )

async def run_test():
    print(f"Running cleanup test in {TEST_WORKSPACE}")
    
    # Mock API key if missing (logic check only)
    if not os.getenv("ANTHROPIC_AUTH_TOKEN"):
        print("Warning: No API Key, logic will fail at network step but path resolution is tested.")
    
    try:
        result = await cleanup_report_async(TEST_WORKSPACE)
        print("--- Result ---")
        print(result)
        print("--------------")
        
        # Verify warnings were caught (if network call succeeded or mocked, but here we expect network failure or real run)
        # If we have a key, we might see the cleaning.
        # But specifically, we want to see if it runs without crashing.
        
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
