import asyncio
import shutil
from pathlib import Path
from universal_agent.gateway import InProcessGateway

async def test_session_persistence():
    print("🧪 Testing Session Persistence...")
    
    # Setup clean workspace
    workspace_base = Path("TEST_WORKSPACES")
    if workspace_base.exists():
        shutil.rmtree(workspace_base)
    workspace_base.mkdir()
    
    gateway = InProcessGateway(workspace_base=workspace_base)
    user_id = "test_user_123"
    session_id_target = f"tg_{user_id}"
    
    print(f"\n1. Attempting to create session for {user_id} expecting ID: {session_id_target}")
    
    # FIXED LOGIC CHECK:
    # We now explicitly pass workspace_dir to force the session ID.
    workspace_dir = str(workspace_base / session_id_target)
    
    # Current behavior check:
    session = await gateway.create_session(user_id=f"telegram_{user_id}", workspace_dir=workspace_dir)
    print(f"   Created Session ID: {session.session_id}")
    print(f"   Workspace: {session.workspace_dir}")
    
    if session.session_id != session_id_target:
        print("   ❌ FAILURE: Session ID does not match target. Persistence will fail.")
    else:
        print("   ✅ SUCCESS: Session ID matches target!")

    # Verify resume fails if ID didn't match
    print(f"\n2. Attempting to resume session: {session_id_target}")
    try:
        resumed = await gateway.resume_session(session_id_target)
        print(f"   ✅ Resumed Session ID: {resumed.session_id}")
    except ValueError:
        print(f"   ❌ FAILURE: Could not resume session {session_id_target}")

    # Cleanup
    if workspace_base.exists():
        shutil.rmtree(workspace_base)

if __name__ == "__main__":
    asyncio.run(test_session_persistence())
