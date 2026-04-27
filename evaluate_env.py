import os

from universal_agent.infisical_loader import initialize_runtime_secrets

initialize_runtime_secrets(profile="local_workstation")

print("UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED", os.getenv("UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED"))
print("UA_MORNING_REPORT_ENABLED", os.getenv("UA_MORNING_REPORT_ENABLED"))
print("UA_HEARTBEAT_AUTONOMOUS_ENABLED", os.getenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED"))
print("UA_MORNING_REPORT_HOUR", os.getenv("UA_MORNING_REPORT_HOUR"))
