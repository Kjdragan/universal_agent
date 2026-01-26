
import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    print("Attempting to import universal_agent.gateway...")
    from universal_agent.gateway import Gateway
    print("Import SUCCESS")
except Exception as e:
    print(f"Import FAILED: {e}")
    import traceback
    traceback.print_exc()

try:
    print("\nAttempting to import universal_agent.agent_core...")
    from universal_agent.agent_core import EventType
    print("Import SUCCESS")
except Exception as e:
    print(f"Import FAILED: {e}")
    import traceback
    traceback.print_exc()
