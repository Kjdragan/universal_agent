
import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.append(os.path.abspath("src"))

from universal_agent.heartbeat_service import HeartbeatService, HeartbeatState, HeartbeatScheduleConfig, HeartbeatDeliveryConfig, HeartbeatVisibilityConfig

# Mock Gateway Session
class MockSession:
    def __init__(self, workspace):
        self.session_id = "debug_session"
        self.workspace_dir = workspace

class MockGateway:
    async def execute(self, session, request):
        print(f"--- MOCK GATEWAY EXECUTE ---")
        print(f"Prompt: {request.user_input}")
        print(f"----------------------------")
        # We can't actually run the LLM here easily without full bootstrap.
        # But this script is to test the Service Logic, not the LLM.
        # Wait... the user wants to know why the AGENT isn't doing anything.
        # The agent relies on main.py/gateway to run.
        yield type("Event", (), {"type": "text", "data": "DEBUG_RESPONSE_FROM_MOCK"})

async def debug_it():
    print("🚀 Starting Force Debug...")
    workspace = "/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260204_224807_23311b1f"
    
    service = HeartbeatService(MockGateway(), None)
    
    # 1. Load State
    session = MockSession(workspace)
    state_path = Path(workspace) / "heartbeat_state.json"
    
    print(f"📂 Workspace: {workspace}")
    if state_path.exists():
        print(f"📄 State File Exists: {state_path.read_text()}")
    else:
        print("❌ State File Missing")
        
    # 2. Check HEARTBEAT.md
    hb_file = Path(workspace) / "HEARTBEAT.md"
    if hb_file.exists():
        print(f"📄 HEARTBEAT.md Content:\n{hb_file.read_text()}")
    else:
        print("❌ HEARTBEAT.md Missing from Workspace!")

    # 3. Simulate Schedule Check
    # We will manually call the internal check logic if possible, or just print config.
    overrides = service._load_json_overrides(Path(workspace))
    schedule = service._resolve_schedule(overrides)
    print(f"⚙️ Schedule Config: {schedule}")
    
    # 4. Check Effectiveness
    # Access the private static method via the class
    is_empty = HeartbeatService._is_effectively_empty(content)
    print(f"🤔 is_effectively_empty: {is_empty}")
    
    if is_empty:
        print("❌ AGENT THINKS FILE IS EMPTY! (That's the bug if true)")

if __name__ == "__main__":
    asyncio.run(debug_it())
