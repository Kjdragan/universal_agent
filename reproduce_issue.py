
import os
import sys
from pathlib import Path

# Add src to path
sys.path.append("/home/kjdragan/lrepos/universal_agent/src")

# Set up environment if needed (mimic gateway_server.py)
os.environ["UA_SKILLS_DIR"] = "/home/kjdragan/lrepos/universal_agent/.claude/skills"
os.environ["UA_OPS_CONFIG_PATH"] = "/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/ops_config.json"

from universal_agent.gateway_server import _load_skill_catalog

print("Attempting to load skill catalog...")
try:
    skills = _load_skill_catalog()
    print(f"Successfully loaded {len(skills)} skills:")
    for s in skills:
        print(f" - {s['name']} (enabled={s.get('enabled')}, available={s.get('available')})")
        if not s.get('available'):
             print(f"   Reason: {s.get('unavailable_reason')}")

except Exception as e:
    print(f"CRASHED: {e}")
    import traceback
    traceback.print_exc()
