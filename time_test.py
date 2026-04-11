import time
import sys

# Try matching the exact sys path setup in server if needed, or simply testing from local.
sys.path.insert(0, '/home/kjdragan/lrepos/universal_agent/src')

try:
    t0 = time.time()
    from universal_agent.runtime_env import runtime_tool_status
    print("import runtime_tool_status took:", time.time() - t0)

    t0 = time.time()
    from universal_agent import get_logfire_runtime_state
    print("import get_logfire_runtime_state took:", time.time() - t0)

    t0 = time.time()
    import universal_agent.main as main_module
    print("import main_module took:", time.time() - t0)
    
except Exception as e:
    print("Error:", e)
