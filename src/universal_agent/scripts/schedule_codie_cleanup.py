"""Script to register the nightly CODIE Proactive Cleanup cron job."""

import os
import sys
import time
from pathlib import Path

# Add project root/src to path for imports
BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.append(str(BASE_DIR / "src"))

from universal_agent.cron_service import CronService
from universal_agent.gateway import InProcessGateway

def schedule_codie_nightly():
    workspaces_dir = BASE_DIR / "AGENT_RUN_WORKSPACES"
    
    # We use a dummy gateway since we just want to save the job to the store
    # CronService requires it for init, but we only use add_job which calls save_jobs
    dummy_gateway = InProcessGateway(BASE_DIR)
    
    service = CronService(
        gateway=dummy_gateway,
        workspaces_dir=workspaces_dir
    )
    
    command = (
        "Run the CODIE proactive cleanup pipeline. Use the internal Python helper "
        "`universal_agent.services.proactive_codie.queue_cleanup_task(conn)` to pick a "
        "low-hanging fruit cleanup theme and dispatch the code cleanup task into the Task Hub. "
        "Afterward, actively process the newly queued Task Hub item to inspect the codebase, "
        "build the required cleanup patches, generate the GitHub PR, and finally, "
        "send the email report via AgentMail."
    )
    
    # Schedule for 1:30 AM daily
    job = service.add_job(
        user_id="kjdragan",
        workspace_dir=str(workspaces_dir / "cron_codie_cleanup"),
        command=command,
        cron_expr="30 1 * * *", 
        timezone="America/Chicago",
        delete_after_run=False,
        metadata={
            "project": "Universal Agent Maintenance",
            "type": "Code Cleanup",
            "scheduled_by": "Antigravity"
        }
    )
    
    print(f"✅ Scheduled CODIE proactive cleanup job (ID: {job.job_id})")
    print(f"🕒 Cron Expression: {job.cron_expr} (America/Chicago)")
    print(f"📁 Workspace: {job.workspace_dir}")
    print(f"📅 Next Run At: {time.ctime(job.next_run_at) if job.next_run_at else 'Unknown'}")

if __name__ == "__main__":
    schedule_codie_nightly()
