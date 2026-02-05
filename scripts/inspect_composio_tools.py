
import asyncio
import os
import sys
from dotenv import load_dotenv

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))

from universal_agent.agent_setup import AgentSetup

async def main():
    load_dotenv()
    
    print("⏳ Initializing AgentSetup...")
    setup = AgentSetup(workspace_dir="/tmp/test_workspace", verbose=True)
    await setup.initialize()
    
    print("\n🔍 Inspecting Composio MCP tools...")
    # The session object has the MCP URL
    print(f"MCP URL: {setup.session.mcp.url}")
    
    # We can't easily query the MCP server directly without an MCP client, 
    # but we can look at what Apps were discovered.
    print(f"\n📱 Discovered Apps: {setup._discovered_apps}")
    
    # Check if we can find tool definitions in the session object
    # The Composio SDK might have a method to list tools for the session
    try:
        # Try to fetch tools via SDK if possible
        print("\n🛠️ Fetching active tools via SDK...")
        # Note: This might not be the exact API, but let's try to see if we can get tool info
        # tools = await setup.composio.get_tools(setup.session.id) # Hypothetical
        pass
    except Exception as e:
        print(f"SDK tool fetch failed: {e}")

    # Let's check if we can list actions for the 'composio_search' app
    try:
        print("\n🔎 Checking 'composio_search' actions...")
        # This is where we might find COMPOSIO_SEARCH_TOOLS if it's an action
        # We'd need to consult the SDK docs or inspect the client
    except Exception:
        pass

    print("\n✅ Inspection complete.")

if __name__ == "__main__":
    asyncio.run(main())
