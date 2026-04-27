import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import sys
import uuid

import httpx

from universal_agent.infisical_loader import initialize_runtime_secrets

logger = logging.getLogger(__name__)

async def main():
    # 1. Initialize runtime secrets via Infisical (allowing dotenv fallback)
    initialize_runtime_secrets(profile="local_workstation")
    logging.basicConfig(level=logging.INFO)
    
    port = os.getenv("UA_GATEWAY_PORT", "8008")
    gateway_url = f"http://127.0.0.1:{port}/api/v1/ops/telemetry/briefing"
    
    api_key = os.getenv("UA_OPS_TOKEN", "")
    if not api_key:
        logger.error("UA_OPS_TOKEN is required to fetch telemetry.")
        sys.exit(1)
        
    headers = {"Authorization": f"Bearer {api_key}"}
    
    logger.info(f"Fetching telemetry from gateway at {gateway_url}...")
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            resp = await client.get(gateway_url)
            resp.raise_for_status()
            data = resp.json()
            briefing_data = data.get("briefing_data", {})
    except Exception as exc:
        logger.error(f"Failed to fetch telemetry from gateway: {exc}")
        sys.exit(1)
        
    artifacts_dir = os.getenv("UA_ARTIFACTS_DIR", "").strip() or "/home/kjdragan/lrepos/universal_agent/artifacts"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    telemetry_json = json.dumps(briefing_data, indent=2)
    
    wiki_content = ""
    wiki_file = os.path.join(artifacts_dir, "nightly_wikis", f"nightly_wiki_{today}.md")
    if os.path.exists(wiki_file):
        try:
            with open(wiki_file, "r") as f:
                wiki_content = f.read()
        except Exception as exc:
            logger.warning(f"Failed to read wiki content: {exc}")

    objective = f"""Generate the daily autonomous operations briefing for the last 24 hours.
Focus only on work executed without direct user prompting (scheduled/proactive flows).

Here is the raw telemetry data:
```json
{telemetry_json}
```

Here is the external Nightly Wiki Proactive Generation output (if any):
```markdown
{wiki_content}
```

Instructions:
- Summarize tasks completed, attempted, and failed.
- Include links/paths to any artifacts produced.
- MUST explicitly include a "Latest Proactive Knowledge Base Additions" section referencing the Nightly Wiki external paths and files if any are provided.
- Highlight any items requiring user decisions.
- If there are data quality warnings, mention them.

Write a concise markdown report to '{artifacts_dir}/autonomous-briefings/{today}/DAILY_BRIEFING.md'.
Make sure to provide a short completion message suitable for a dashboard notification.
"""
    
    logger.info("Dispatching mission to vp.general.primary...")

    from universal_agent.tools.vp_orchestration import dispatch_vp_mission

    try:
        await dispatch_vp_mission(
            objective=objective,
            mission_type="briefing",
            idempotency_key=f"briefing-{today}",
        )
    except RuntimeError as exc:
        logger.error(f"Dispatch failed: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
