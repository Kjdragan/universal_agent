import asyncio
import os
import sys
from pathlib import Path
import logging

from universal_agent.cron_service import CronService, CronStore, CronJob

logger = logging.getLogger(__name__)

async def main():
    logging.basicConfig(level=logging.INFO)
    
    workspaces_dir = Path(os.getenv("UA_WORKSPACES_DIR", "/home/kjdragan/lrepos/universal_agent/workspaces"))
    
    # We can just use the store directly to upsert the job
    jobs_path = workspaces_dir / "cron_jobs.json"
    runs_path = workspaces_dir / "cron_runs.jsonl"
    
    store = CronStore(jobs_path, runs_path)
    jobs = store.load_jobs()
    
    # Check if job already exists
    PROACTIVE_JOB_ID = "nightly_wiki"
    
    command = "uv run python -m universal_agent.scripts.nightly_wiki_agent"
    
    if PROACTIVE_JOB_ID in jobs:
        logger.info(f"Cron job {PROACTIVE_JOB_ID} already exists. Updating...")
        job = jobs[PROACTIVE_JOB_ID]
        job.command = command
        job.cron_expr = "0 3 * * *"
        job.timezone = "America/Chicago"  # Use local time for 3 AM
        job.model = None
    else:
        logger.info(f"Creating new cron job {PROACTIVE_JOB_ID}...")
        job = CronJob(
            job_id=PROACTIVE_JOB_ID,
            user_id="system",
            workspace_dir=str(workspaces_dir / f"cron_{PROACTIVE_JOB_ID}"),
            command=command,
            cron_expr="0 3 * * *",
            timezone="America/Chicago",
        )
        jobs[PROACTIVE_JOB_ID] = job
        
    store.save_jobs(jobs.values())
    logger.info("Successfully scheduled nightly_wiki job for 3:00 AM daily.")

if __name__ == "__main__":
    asyncio.run(main())
