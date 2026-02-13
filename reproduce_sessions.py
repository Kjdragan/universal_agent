
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add src to path
sys.path.append("/home/kjdragan/lrepos/universal_agent/src")

# Set up environment
workspaces_dir = Path("/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES").resolve()
os.environ["UA_WORKSPACES_DIR"] = str(workspaces_dir)

from universal_agent.ops_service import OpsService

# Mock Gateway
mock_gateway = MagicMock()
mock_gateway._sessions = {} # Mock active sessions

print(f"Testing OpsService.list_sessions with workspaces_dir={workspaces_dir}")


try:
    service = OpsService(mock_gateway, workspaces_dir)
    print("Testing session iteration...")
    
    # Iterate manually to catch per-session errors
    if not workspaces_dir.exists():
        print(f"ERROR: Workspaces dir does not exist: {workspaces_dir}")
        sys.exit(1)
        
    session_dirs = [p for p in workspaces_dir.iterdir() if p.is_dir()]
    print(f"Found {len(session_dirs)} session directories.")
    
    for p in session_dirs:
        try:
            summary = service._build_session_summary(p)
            print(f"SUCCESS: {p.name} -> {summary['status']}")
        except Exception as e:
            print(f"CRASHED on {p.name}: {e}")
            import traceback
            traceback.print_exc()

except Exception as e:
    print(f"CRASHED globally: {e}")
    import traceback
    traceback.print_exc()
