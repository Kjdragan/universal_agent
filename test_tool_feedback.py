import json
import os
import sys
import asyncio
from pathlib import Path

# Add src to sys.path
src_dir = os.path.dirname(os.path.abspath(__file__)) + "/src"
sys.path.insert(0, src_dir)

# Mock _resolve_workspace
import mcp_server
os.environ["CURRENT_SESSION_WORKSPACE"] = "/tmp/ua_test_workspace"
workspace = "/tmp/ua_test_workspace"
os.makedirs(workspace, exist_ok=True)
os.makedirs(os.path.join(workspace, "search_results"), exist_ok=True)
# Add a dummy json to pass initial check
with open(os.path.join(workspace, "search_results", "test.json"), "w") as f:
    f.write('{"test": true}')

async def test_responses():
    print("Testing _run_research_phase_legacy...")
    # Mock finalize_research to avoid long crawl
    async def mock_finalize(*args, **kwargs):
        return "success"
    mcp_server.finalize_research = mock_finalize
    
    resp = await mcp_server._run_research_phase_legacy(query="test", task_name="test_task")
    print(f"Response: {resp}")
    try:
        data = json.loads(resp)
        assert data["status"] == "success"
        assert "refined_corpus" in data["outputs"]
        print("✅ Research phase response is valid JSON.")
    except Exception as e:
        print(f"❌ Research phase response failed: {e}")

    print("\nTesting _run_report_generation_legacy...")
    # Create dummy corpus
    corpus_path = os.path.join(workspace, "tasks", "test_task", "refined_corpus.md")
    os.makedirs(os.path.dirname(corpus_path), exist_ok=True)
    with open(corpus_path, "w") as f:
        f.write("test corpus")
        
    async def mock_outline(*args, **kwargs): return "success"
    async def mock_draft(*args, **kwargs): return "success"
    async def mock_cleanup(*args, **kwargs): return "success"
    def mock_compile(*args, **kwargs): return "success summary"
    
    mcp_server.generate_outline = mock_outline
    mcp_server.draft_report_parallel = mock_draft
    mcp_server.cleanup_report = mock_cleanup
    mcp_server.compile_report = mock_compile
    
    resp = await mcp_server._run_report_generation_legacy(query="test", task_name="test_task")
    print(f"Response: {resp}")
    try:
        data = json.loads(resp)
        assert data["status"] == "success"
        assert "report_html" in data["outputs"]
        print("✅ Report generation response is valid JSON.")
    except Exception as e:
        print(f"❌ Report generation response failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_responses())
