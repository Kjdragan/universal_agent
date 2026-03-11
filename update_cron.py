import json

with open("AGENT_RUN_WORKSPACES/cron_jobs.json", "r") as f:
    data = json.load(f)

for job in data["jobs"]:
    if job.get("metadata", {}).get("system_job") == "autonomous_daily_briefing":
        job["command"] = "!script universal_agent/scripts/briefings_agent.py"

from time import time
import uuid

new_job = {
    "job_id": str(uuid.uuid4())[:10],
    "user_id": "cron_system",
    "workspace_dir": "/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/cron_knowledge_base_maintenance",
    "command": "!script universal_agent/scripts/doc_maintenance_agent.py",
    "every_seconds": 0,
    "cron_expr": "0 2 * * *",
    "timezone": "UTC",
    "run_at": None,
    "delete_after_run": False,
    "model": None,
    "timeout_seconds": 1200,
    "enabled": True,
    "created_at": time(),
    "last_run_at": None,
    "next_run_at": time() + 86400,
    "metadata": {
        "system_job": "knowledge_base_maintenance",
        "description": "Autonomously updates documentation based on git stats"
    }
}

# Add if not already present
if not any(j.get("metadata", {}).get("system_job") == "knowledge_base_maintenance" for j in data["jobs"]):
    data["jobs"].append(new_job)

with open("AGENT_RUN_WORKSPACES/cron_jobs.json", "w") as f:
    json.dump(data, f, indent=2)

print("Updated cron jobs matching!")
