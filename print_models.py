from universal_agent.infisical_loader import load_infisical_environment
import os

load_infisical_environment("development", "local_workstation")

for k, v in os.environ.items():
    if "MODEL" in k or k == "API_KEY" or "ANTHROPIC" in k or "CLAUDE" in k:
        # omit key values
        if "KEY" in k: v = "..." 
        print(f"{k}={v}")
