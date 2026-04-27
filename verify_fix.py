import asyncio
import os
from pathlib import Path
import sys

# Add src to path
sys.path.append(str(Path(__file__).parent / "src"))

from universal_agent.agent_core import EventType, UniversalAgent, ctx_session_id


async def verify_session_info():
    print("🚀 Verifying SESSION_INFO and Logging Context...")
    
    agent = UniversalAgent(user_id="test_user")
    await agent.initialize()
    
    # workspace_dir name should be our session_id
    expected_session_id = os.path.basename(agent.workspace_dir)
    print(f"DEBUG: Expected Session ID: {expected_session_id}")
    
    events = []
    
    # We only need the first few events
    async def run():
        async for event in agent.run_query("hi"):
            events.append(event)
            if event.type == EventType.SESSION_INFO:
                print(f"✅ Received SESSION_INFO: {event.data}")
                
                # Check session_id alignment
                session_id = event.data.get("session_id")
                if session_id == expected_session_id:
                    print(f"✅ Session ID matches workspace: {session_id}")
                else:
                    print(f"❌ Session ID mismatch! Got {session_id}, expected {expected_session_id}")
                
                # Check version
                version = event.data.get("version")
                if version == "v2.1":
                    print(f"✅ Version is correct: {version}")
                else:
                    print(f"❌ Version mismatch! Got {version}, expected v2.1")
                
                # Check context variable
                ctx_id = ctx_session_id.get()
                if ctx_id == expected_session_id:
                    print(f"✅ Context session_id matches: {ctx_id}")
                else:
                    print(f"❌ Context session_id mismatch! Got {ctx_id}, expected {expected_session_id}")
                
                break # We got what we needed
    
    try:
        await asyncio.wait_for(run(), timeout=10.0)
    except asyncio.TimeoutError:
        print("❌ Timeout waiting for events")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await agent.close()

if __name__ == "__main__":
    asyncio.run(verify_session_info())
