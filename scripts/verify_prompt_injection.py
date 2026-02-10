import os
import sys
from unittest.mock import MagicMock, patch

# Add src to path
current_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.dirname(current_dir)
sys.path.append(os.path.join(repo_root, "src"))
sys.path.append(repo_root)

# Mock environment variables
os.environ["COMPOSIO_API_KEY"] = "mock_key"
os.environ["LOGFIRE_TOKEN"] = "mock_token"

async def test_prompt_injection():
    from universal_agent.main import setup_session
    
    # Mock some dependencies to avoid actual network/file calls that might fail
    with patch("universal_agent.main.Composio"), \
         patch("universal_agent.main.resolve_user_id", return_value="test_user"), \
         patch("universal_agent.utils.composio_discovery.discover_connected_toolkits", return_value=["github"]), \
         patch("universal_agent.utils.composio_discovery.get_local_tools", return_value=[]), \
         patch("universal_agent.main.discover_skills", return_value=[]), \
         patch("universal_agent.main.generate_skills_xml", return_value="<skills></skills>"), \
         patch("universal_agent.main.get_tool_knowledge_block", return_value="Knowledge block"), \
         patch("universal_agent.main.get_tool_knowledge_content", return_value="Knowledge content"), \
         patch("universal_agent.main.open_run_log"):
        
        print("Testing setup_session...")
        options, session, user_id, workspace_dir, trace, agent = await setup_session(
            workspace_dir_override="/tmp/ua_test_workspace",
            attach_stdio=False
        )
        
        print(f"System Prompt length: {len(options.system_prompt)}")
        
        if "## 🧠 YOUR CAPABILITIES & SPECIALISTS" in options.system_prompt:
            print("✅ FOUND capabilities header in system prompt")
        else:
            print("❌ MISSING capabilities header in system prompt")
            
        if "research-specialist" in options.system_prompt:
             print("✅ FOUND specialist 'research-specialist' in system prompt")
        else:
             print("❌ MISSING specialist 'research-specialist' in system prompt")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_prompt_injection())
