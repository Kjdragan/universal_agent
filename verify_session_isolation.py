import asyncio
import os
import sys
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add src to sys.path
src_dir = os.path.join(os.getcwd(), "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from universal_agent.execution_engine import ProcessTurnAdapter, EngineConfig
from universal_agent.agent_core import EventType

async def test_session_persistence():
    print("--- Testing Session Persistence ---")
    workspace = os.path.abspath("AGENT_RUN_WORKSPACES/test_persistence")
    os.makedirs(workspace, exist_ok=True)
    
    config = EngineConfig(workspace_dir=workspace, user_id="test_user")
    adapter = ProcessTurnAdapter(config)
    await adapter.initialize()
    
    print("Turn 1: Setting context...")
    # We'll use a prompt that leaves a trace we can check for
    async for event in adapter.execute("Remember this secret code: 'BANANA-APPLE-123'"):
        if event.type == EventType.TEXT:
            print(f"Agent: {event.data.get('text')}")
            
    print("\nTurn 2: Checking context...")
    found_secret = False
    async for event in adapter.execute("What was the secret code I told you?"):
        if event.type == EventType.TEXT:
            response = event.data.get('text', '')
            print(f"Agent: {response}")
            if "BANANA-APPLE-123" in response:
                found_secret = True
                
    if found_secret:
        print("✅ SUCCESS: History persisted across turns.")
    else:
        print("❌ FAILURE: History lost!")

    print("\nTurn 3: Testing /reset...")
    async for event in adapter.execute("/reset"):
        if event.type == EventType.TEXT:
             print(f"Agent: {event.data.get('text')}")

    print("\nTurn 4: Verifying reset...")
    found_secret_after_reset = False
    async for event in adapter.execute("What was the secret code I told you?"):
        if event.type == EventType.TEXT:
            response = event.data.get('text', '')
            print(f"Agent: {response}")
            if "BANANA-APPLE-123" in response:
                found_secret_after_reset = True
    
    if not found_secret_after_reset:
        print("✅ SUCCESS: Session correctly cleared after /reset.")
    else:
        print("❌ FAILURE: Session still has history after /reset!")

    await adapter.close()

async def test_workspace_isolation():
    print("\n--- Testing Workspace Isolation ---")
    ws1 = os.path.abspath("AGENT_RUN_WORKSPACES/ws1")
    ws2 = os.path.abspath("AGENT_RUN_WORKSPACES/ws2")
    os.makedirs(ws1, exist_ok=True)
    os.makedirs(ws2, exist_ok=True)
    
    from universal_agent.execution_context import get_current_workspace
    
    async def run_session(ws_path, name):
        config = EngineConfig(workspace_dir=ws_path, user_id=f"user_{name}")
        adapter = ProcessTurnAdapter(config)
        await adapter.initialize()
        
        # In a real concurrent scenario, get_current_workspace() should return the correct path
        # within this async task if ContextVar is working and bounded.
        # But wait, we need to make sure the adapter calls bind_workspace within the task context.
        # execution_engine calls process_turn, which calls bind_workspace_env internally!
        
        async for event in adapter.execute(f"I am in {name}. Write a file 'me.txt' with my name."):
            pass
            
        current_ws = get_current_workspace()
        print(f"  [{name}] ContextVar says workspace is: {current_ws}")
        
        await adapter.close()
        return current_ws

    # Run concurrently
    results = await asyncio.gather(
        run_session(ws1, "Session-1"),
        run_session(ws2, "Session-2")
    )
    
    if results[0] == ws1 and results[1] == ws2:
        print("✅ SUCCESS: ContextVars correctly isolated concurrent workspaces.")
    else:
        print(f"❌ FAILURE: Workspaces leaked! Got: {results}")

if __name__ == "__main__":
    asyncio.run(test_session_persistence())
    asyncio.run(test_workspace_isolation())
