
import asyncio
import os
import shutil
import json
from pathlib import Path
from universal_agent.scripts.parallel_draft import draft_report_async

# Create a dummy workspace for testing
TEST_WORKSPACE = Path("/tmp/test_workspace_draft_verif")
TEST_WORKSPACE.mkdir(parents=True, exist_ok=True)

# Create dummy outline
(TEST_WORKSPACE / "work_products" / "_working").mkdir(parents=True, exist_ok=True)
outline_path = TEST_WORKSPACE / "work_products" / "_working" / "outline.json"
outline_content = {
    "sections": [
        {"id": "intro", "title": "Introduction"},
        {"id": "conclusion", "title": "Conclusion"}
    ]
}
outline_path.write_text(json.dumps(outline_content))

# Create dummy corpus
(TEST_WORKSPACE / "tasks" / "test_task").mkdir(parents=True, exist_ok=True)
corpus_path = TEST_WORKSPACE / "tasks" / "test_task" / "refined_corpus.md"
corpus_path.write_text("# Dummy Corpus\n\nSome research content here.")

async def run_test():
    print(f"Running test in {TEST_WORKSPACE}")
    
    # Mock Anthropic Client to avoid API calls if possible, or just allow it to fail gracefully if no key 
    # But wait, the script uses AsyncAnthropic directly. 
    # For a true verification without hitting API, we'd mock it. 
    # However, since I have full access, if I have the API key in env it will run.
    # Let's assume we want to verify the PATH resolution logic primarily.
    
    if not os.getenv("ANTHROPIC_AUTH_TOKEN") and not os.getenv("ZAI_API_KEY"):
         print("Warning: No API key found. Detailed generation will fail, but path logic will run.")
    
    try:
        result = await draft_report_async(TEST_WORKSPACE)
        print(f"Result: {result}")
        
        # Check if output directory was created
        out_dir = TEST_WORKSPACE / "work_products" / "_working" / "sections"
        print(f"Output dir exists: {out_dir.exists()}")
        
    except Exception as e:
        print(f"Test failed with exception: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
