
import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from universal_agent.agent_setup import AgentSetup

async def main():
    print("🚀 Verifying Capabilities Generation...")
    
    # Setup test workspace
    workspace = "/tmp/test_capabilities_workspace"
    os.makedirs(workspace, exist_ok=True)
    
    # Initialize AgentSetup
    # Initialize AgentSetup
    # Use real user ID to discovery actual connected apps
    setup = AgentSetup(workspace_dir=workspace, user_id=None, enable_skills=True, verbose=True)
    
    # Using mock Composio client to avoid actual network calls if possible, 
    # but here we want to test the real integration with our changes.
    # However, running without API key might fail on connect.
    # Let's assume env vars are set or we handle failure gracefully.
    
    try:
        await setup.initialize()
        print("\n✅ Initialization Complete.")
    except Exception as e:
        print(f"\n⚠️ Initialization warned/failed: {e}")
        
    # Check if capabilities.md exists
    cap_path = os.path.join(setup.src_dir, "src", "universal_agent", "prompt_assets", "capabilities.md")
    if os.path.exists(cap_path):
        print(f"\n✅ Capabilities file found at: {cap_path}")
        print("-" * 50)
        with open(cap_path, "r", encoding="utf-8") as f:
            content = f.read()
            print(content)
        print("-" * 50)
        
        # Verify specific content
        if "Gmail" in content and "email service" in content: 
             print("✅ Gmail description found!")
        else:
             print("❌ Gmail description MISSING.")
             
        if "Executes Python code" in content:
             print("✅ CodeInterpreter description found!")
        else:
             print("❌ CodeInterpreter description MISSING.")

    else:
        print(f"\n❌ Capabilities file NOT found at: {cap_path}")

if __name__ == "__main__":
    asyncio.run(main())
