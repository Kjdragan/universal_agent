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
    
    # ── 1. Nightly Wiki job (3:15 AM Houston) ────────────────────────────
    # Uses !script prefix so CronService runs it as a subprocess, not an LLM prompt.
    WIKI_JOB_ID = "nightly_wiki"
    wiki_command = "!script universal_agent.scripts.nightly_wiki_agent"
    
    if WIKI_JOB_ID in jobs:
        logger.info(f"Cron job {WIKI_JOB_ID} already exists. Updating...")
        job = jobs[WIKI_JOB_ID]
        job.command = wiki_command
        job.cron_expr = "15 3 * * *"
        job.timezone = "America/Chicago"
        job.model = None
        job.timeout_seconds = 1800
    else:
        logger.info(f"Creating new cron job {WIKI_JOB_ID}...")
        job = CronJob(
            job_id=WIKI_JOB_ID,
            user_id="system",
            workspace_dir=str(workspaces_dir / f"cron_{WIKI_JOB_ID}"),
            command=wiki_command,
            cron_expr="15 3 * * *",
            timezone="America/Chicago",
            timeout_seconds=1800,
        )
        jobs[WIKI_JOB_ID] = job

    # ── 2. Morning Briefing job (6:30 AM Houston) ────────────────────────
    BRIEFING_JOB_ID = "morning_briefing"
    briefing_command = "!script universal_agent.scripts.briefings_agent"
    
    if BRIEFING_JOB_ID in jobs:
        logger.info(f"Cron job {BRIEFING_JOB_ID} already exists. Updating...")
        bjob = jobs[BRIEFING_JOB_ID]
        bjob.command = briefing_command
        bjob.cron_expr = "30 6 * * *"
        bjob.timezone = "America/Chicago"
        bjob.model = None
        bjob.timeout_seconds = 900
    else:
        logger.info(f"Creating new cron job {BRIEFING_JOB_ID}...")
        bjob = CronJob(
            job_id=BRIEFING_JOB_ID,
            user_id="system",
            workspace_dir=str(workspaces_dir / f"cron_{BRIEFING_JOB_ID}"),
            command=briefing_command,
            cron_expr="30 6 * * *",
            timezone="America/Chicago",
            timeout_seconds=900,
        )
        jobs[BRIEFING_JOB_ID] = bjob
        
    store.save_jobs(jobs.values())
    logger.info("Successfully scheduled nightly_wiki (3:15 AM) and morning_briefing (6:30 AM) jobs.")

if __name__ == "__main__":
    asyncio.run(main())
