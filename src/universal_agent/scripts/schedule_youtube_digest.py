#!/usr/bin/env python3
"""Register the Daily YouTube Digest cron job."""

import asyncio
import logging
import os
from pathlib import Path

from universal_agent.cron_service import CronJob, CronStore

logger = logging.getLogger(__name__)

async def main():
    logging.basicConfig(level=logging.INFO)
    
    workspaces_dir = Path(os.getenv("UA_WORKSPACES_DIR", "/home/kjdragan/lrepos/universal_agent/workspaces"))
    
    jobs_path = workspaces_dir / "cron_jobs.json"
    runs_path = workspaces_dir / "cron_runs.jsonl"
    
    store = CronStore(jobs_path, runs_path)
    jobs = store.load_jobs()
    
    JOB_ID = "daily_youtube_digest"
    command = "!script universal_agent.scripts.youtube_daily_digest"
    
    if JOB_ID in jobs:
        logger.info(f"Cron job {JOB_ID} already exists. Updating...")
        job = jobs[JOB_ID]
        job.command = command
        job.cron_expr = "0 6 * * *"  # 6:00 AM Daily
        job.timezone = "America/Chicago"
        job.model = None
        job.timeout_seconds = 3600  # Give it an hour for multiple transcripts
    else:
        logger.info(f"Creating new cron job {JOB_ID}...")
        job = CronJob(
            job_id=JOB_ID,
            user_id="system",
            workspace_dir=str(workspaces_dir / f"cron_{JOB_ID}"),
            command=command,
            cron_expr="0 6 * * *",
            timezone="America/Chicago",
            timeout_seconds=3600,
        )
        jobs[JOB_ID] = job

    store.save_jobs(jobs.values())
    logger.info(f"Successfully registered {JOB_ID}. Next runs will happen at 6:00 AM Central Time.")

if __name__ == "__main__":
    asyncio.run(main())
