
import os
import sys
import time
from pathlib import Path

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

print("Before import...")
start_time = time.time()
existing_sessions = set()
workspace_root = Path("AGENT_RUN_WORKSPACES")
if workspace_root.exists():
    existing_sessions = set(p.name for p in workspace_root.iterdir())

try:
    import universal_agent.main
    print("Import successful.")
except ImportError as e:
    print(f"Import failed: {e}")
except Exception as e:
    print(f"Execution during import: {e}")

# Check for new sessions
if workspace_root.exists():
    current_sessions = set(p.name for p in workspace_root.iterdir())
    new_sessions = current_sessions - existing_sessions
    if new_sessions:
        print(f"SIDE EFFECT DETECTED: New sessions created: {new_sessions}")
    else:
        print("No new sessions created by import.")
else:
    print("Workspace root does not exist.")
