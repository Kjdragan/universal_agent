import logging
import os
from universal_agent.infisical_loader import initialize_runtime_secrets
logging.basicConfig(level=logging.INFO)
res = initialize_runtime_secrets(profile="local_workstation")
print("Result:", res)
print("UA_OPS_TOKEN:", os.getenv("UA_OPS_TOKEN", "not found"))
