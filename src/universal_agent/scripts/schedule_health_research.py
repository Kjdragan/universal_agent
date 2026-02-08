
import os
import sys
import time
from pathlib import Path

# Add project root to path for imports
BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.append(str(BASE_DIR))

from universal_agent.cron_service import CronService, CronStore, parse_run_at
from universal_agent.gateway import InProcessGateway

def schedule_task():
    workspaces_dir = BASE_DIR / "AGENT_RUN_WORKSPACES"
    
    # We use a dummy gateway since we just want to save the job to the store
    # CronService requires it for init, but we only use add_job which calls save_jobs
    dummy_gateway = InProcessGateway(BASE_DIR)
    
    service = CronService(
        gateway=dummy_gateway,
        workspaces_dir=workspaces_dir
    )
    
    command = (
        "Research the feasibility and design of a proactive System Health Monitor Agent. "
        "Investigate the current project setup, internal tools, and Composio connections. "
        "Create a PRD in the OFFICIAL_PROJECT_DOCUMENTATION directory detailing the best approach, "
        "including sub-agents, skills, and periodic scheduling (e.g., once a day/week) to detect "
        "regressions or broken connectors."
    )
    
    # Schedule for 2am tonight (next 2am)
    run_at_ts = parse_run_at("2am", timezone_name="America/Chicago")
    
    job = service.add_job(
        user_id="kjdragan",
        workspace_dir=str(workspaces_dir / "cron_health_research"),
        command=command,
        run_at=run_at_ts,
        delete_after_run=False, # Keep for history until user reviews
        metadata={
            "project": "System Health Monitor",
            "type": "Research/PRD",
            "scheduled_by": "Antigravity"
        },
        timezone="America/Chicago"
    )
    
    print(f"✅ Scheduled health research job (ID: {job.job_id}) at {time.ctime(run_at_ts)}")
    print(f"📁 Workspace: {job.workspace_dir}")

if __name__ == "__main__":
    schedule_task()
