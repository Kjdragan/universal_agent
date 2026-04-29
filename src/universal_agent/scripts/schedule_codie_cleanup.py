"""Script to register the nightly CODIE Proactive Cleanup cron job."""

import os
from pathlib import Path
import sys
import time

# Add project root/src to path for imports
BASE_DIR = Path(__file__).parent.parent.parent.parent
sys.path.append(str(BASE_DIR / "src"))

from universal_agent.cron_service import CronService
from universal_agent.gateway import InProcessGateway

CODIE_CLEANUP_WORKSPACE_NAME = "cron_codie_cleanup"
CODIE_CLEANUP_SYSTEM_JOB = "codie_proactive_cleanup"
CODIE_CLEANUP_COMMAND = "!script universal_agent.scripts.codie_cleanup_enqueue"


def schedule_codie_nightly():
    workspaces_dir = BASE_DIR / "AGENT_RUN_WORKSPACES"
    
    # We use a dummy gateway since we just want to save the job to the store
    # CronService requires it for init, but we only use add_job which calls save_jobs
    dummy_gateway = InProcessGateway(BASE_DIR)
    
    service = CronService(
        gateway=dummy_gateway,
        workspaces_dir=workspaces_dir
    )
    
    workspace_dir = str(workspaces_dir / CODIE_CLEANUP_WORKSPACE_NAME)
    metadata = {
        "project": "Universal Agent Maintenance",
        "type": "Code Cleanup",
        "scheduled_by": "system",
        "system_job": CODIE_CLEANUP_SYSTEM_JOB,
        "autonomous": True,
        "proactive_producer": "codie_cleanup",
        "session_id": CODIE_CLEANUP_WORKSPACE_NAME,
    }

    existing = None
    for candidate in service.list_jobs():
        candidate_meta = getattr(candidate, "metadata", None)
        candidate_workspace = str(getattr(candidate, "workspace_dir", "") or "")
        if isinstance(candidate_meta, dict) and candidate_meta.get("system_job") == CODIE_CLEANUP_SYSTEM_JOB:
            existing = candidate
            break
        if candidate_workspace == workspace_dir:
            existing = candidate
            break
    
    # Schedule for 1:30 AM daily
    if existing is not None:
        job = service.update_job(
            existing.job_id,
            {
                "user_id": "system",
                "workspace_dir": workspace_dir,
                "command": CODIE_CLEANUP_COMMAND,
                "description": "Queue one low-to-medium complexity CODIE cleanup task into Task Hub; execution must end in a PR to develop or a no-PR artifact.",
                "cron_expr": "30 1 * * *",
                "timezone": "America/Chicago",
                "delete_after_run": False,
                "enabled": True,
                "metadata": metadata,
            },
        )
    else:
        job = service.add_job(
            user_id="system",
            workspace_dir=workspace_dir,
            command=CODIE_CLEANUP_COMMAND,
            description="Queue one low-to-medium complexity CODIE cleanup task into Task Hub; execution must end in a PR to develop or a no-PR artifact.",
            cron_expr="30 1 * * *",
            timezone="America/Chicago",
            delete_after_run=False,
            metadata=metadata,
        )
    
    print(f"✅ Scheduled CODIE proactive cleanup job (ID: {job.job_id})")
    print(f"🕒 Cron Expression: {job.cron_expr} (America/Chicago)")
    print(f"📁 Workspace: {job.workspace_dir}")
    print(f"📅 Next Run At: {time.ctime(job.next_run_at) if job.next_run_at else 'Unknown'}")

if __name__ == "__main__":
    schedule_codie_nightly()
