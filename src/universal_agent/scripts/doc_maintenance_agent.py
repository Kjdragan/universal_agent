import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone

from universal_agent.infisical_loader import initialize_runtime_secrets

logger = logging.getLogger(__name__)

async def main():
    # 1. Initialize runtime secrets via Infisical (allowing dotenv fallback)
    initialize_runtime_secrets(profile="local_workstation")
    logging.basicConfig(level=logging.INFO)
    
    # Needs UA_OPS_TOKEN to hit gateways or auth
    api_key = os.getenv("UA_OPS_TOKEN", "")
    if not api_key:
        logger.error("UA_OPS_TOKEN is required for autonomous operations.")
        sys.exit(1)
        
    days = int(os.getenv("UA_DOC_MAINTENANCE_DAYS", "1"))
    logger.info(f"Gathering git diff statistics for the last {days} days...")
    
    try:
        # Get commit hashes for the last N days
        since = f"{days} days ago"
        git_log_cmd = ["git", "log", f"--since={since}", "--pretty=format:%H"]
        log_res = subprocess.run(git_log_cmd, capture_output=True, text=True, check=True)
        commits = log_res.stdout.strip().split("\n")
        
        if not commits or (len(commits) == 1 and not commits[0]):
            logger.info("No commits found in the specified timeframe. Exiting.")
            sys.exit(0)
            
        oldest_commit = commits[-1]
        
        # Get the diff stat against the oldest commit
        diff_cmd = ["git", "diff", "--stat", oldest_commit, "HEAD"]
        diff_res = subprocess.run(diff_cmd, capture_output=True, text=True, check=True)
        diff_stat = diff_res.stdout.strip()
        
        # Optional: Get full diff (might be too large, but useful for context)
        # Using name-status to get list of files
        files_cmd = ["git", "diff", "--name-status", oldest_commit, "HEAD"]
        files_res = subprocess.run(files_cmd, capture_output=True, text=True, check=True)
        files_changed = files_res.stdout.strip()
        
    except subprocess.CalledProcessError as exc:
        logger.error(f"Git command failed: {exc.stderr}")
        sys.exit(1)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M")
    
    objective = f"""You are the Documentation Maintenance Agent. 
Your objective is to read the recent codebase changes and update the documentation in the 'OFFICIAL_PROJECT_DOCUMENTATION/' directory to ensure it remains accurate and up-to-date.

Here is the `git diff --stat` showing the files changed over the last {days} days:
```
{diff_stat}
```

Detailed file status:
```
{files_changed}
```

Instructions:
1. Examine the list of changed backend/frontend files.
2. Read the corresponding documentation files in `OFFICIAL_PROJECT_DOCUMENTATION/` to see if they are stale.
3. If any documentation is outdated regarding architecture, features, or configurations due to these code changes, update the markdown files directly.
4. If no documentation changes are necessary, exit successfully with a note.
5. Provide a summary of the markdown files you modified and why.
"""
    
    logger.info("Dispatching mission to vp.coder.primary...")
    
    from universal_agent.tools.vp_orchestration import _vp_dispatch_mission_impl
    
    result = await _vp_dispatch_mission_impl({
        "vp_id": "vp.coder.primary",
        "objective": objective,
        "mission_type": "doc-maintenance",
        "idempotency_key": f"doc-maintenance-{today}",
        "execution_mode": "sdk",
    })
    
    if result.get("content", [{}])[0].get("text"):
        res_data = json.loads(result["content"][0]["text"])
        if res_data.get("ok"):
            logger.info(f"Successfully dispatched documentation maintenance mission: {res_data.get('mission_id')}")
        else:
            logger.error(f"Failed to dispatch mission: {res_data}")
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
