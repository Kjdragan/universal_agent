
import json
import os
import time
from pathlib import Path

# Config
REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACES_DIR = REPO_ROOT / "AGENT_RUN_WORKSPACES"
CRON_FILE = WORKSPACES_DIR / "cron_jobs.json"

JOB_ID = "weekly_context_check"
JOB_COMMAND = "Check for pending context gaps using the fetch_context_gaps tool. If gaps are found, recommend a Memory Interview."
CRON_SCHEDULE = "0 9 * * 1" # Weekly on Monday at 9:00 AM

def install_cron_job():
    print(f"🔧 Installing Weekly Interview Cron Job...")
    print(f"   Target: {CRON_FILE}")
    
    if not WORKSPACES_DIR.exists():
        print(f"   Creating workspaces directory: {WORKSPACES_DIR}")
        WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
        
    jobs = {}
    if CRON_FILE.exists():
        try:
            with open(CRON_FILE, "r") as f:
                data = json.load(f)
                # Handle list or dict format (CronStore expects {"jobs": [...]})
                if isinstance(data, dict) and "jobs" in data:
                    for j in data["jobs"]:
                        jobs[j["job_id"]] = j
                else:
                    print("⚠️  Warning: Existing cron file format unrecognized. Backing up.")
                    os.rename(CRON_FILE, str(CRON_FILE) + ".bak")
        except Exception as e:
            print(f"⚠️  Error reading existing cron file: {e}")

    # Define the job
    job = {
        "job_id": JOB_ID,
        "user_id": "cron_system",
        "workspace_dir": str(WORKSPACES_DIR / f"cron_{JOB_ID}"), 
        "command": JOB_COMMAND,
        "cron_expr": CRON_SCHEDULE,
        "enabled": True,
        "created_at": time.time(),
        "metadata": {
            "description": "Weekly check for user context gaps",
            "source": "schedule_weekly_interview.py"
        }
    }
    
    # Upsert
    jobs[JOB_ID] = job
    
    # Save
    with open(CRON_FILE, "w") as f:
        json.dump({"jobs": list(jobs.values())}, f, indent=2)
        
    print(f"✅ Cron job '{JOB_ID}' installed successfully.")
    print(f"   Schedule: {CRON_SCHEDULE}")
    print(f"   Command: {JOB_COMMAND}")

if __name__ == "__main__":
    install_cron_job()
