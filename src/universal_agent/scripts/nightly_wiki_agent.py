import asyncio
import json
import logging
import os
import sys
import sqlite3
from datetime import datetime, timezone

from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.proactive_signals import list_cards, CARD_STATUS_PENDING

logger = logging.getLogger(__name__)

async def main():
    # 1. Initialize runtime secrets via Infisical
    initialize_runtime_secrets(profile="local_workstation")
    logging.basicConfig(level=logging.INFO)
    
    # Ensure artifacts directory for wikis exists
    artifacts_dir = os.getenv("UA_ARTIFACTS_DIR", "").strip() or "/home/kjdragan/lrepos/universal_agent/artifacts"
    wiki_artifacts_dir = os.path.join(artifacts_dir, "nightly_wikis")
    os.makedirs(wiki_artifacts_dir, exist_ok=True)
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Connect to DB to load pending cards
    db_path = get_runtime_db_path()
    conn = connect_runtime_db(db_path)
    
    # Read the pending signals
    try:
        cards = list_cards(conn, status=CARD_STATUS_PENDING, limit=100)
    except Exception as exc:
        logger.error(f"Failed to load proactive signals: {exc}")
        sys.exit(1)
    finally:
        conn.close()

    # Determine desired count of wikis to create nightly
    wiki_count_str = os.getenv("UA_DAILY_PROACTIVE_WIKI_COUNT", "1")
    try:
        wiki_count = int(wiki_count_str)
    except ValueError:
        wiki_count = 1

    if not cards:
        logger.info("No pending cards available for proactive wiki generation.")
        sys.exit(0)
    
    cards_context = json.dumps([
        {
            "card_id": c.get("card_id"),
            "source": c.get("source"),
            "title": c.get("title"),
            "summary": c.get("summary")
        } for c in cards[:20]  # Pass top 20 candidates
    ], indent=2)
    
    objective = f"""You are executing the Nightly Proactive Wiki Creation routine.
Your target is to generate {wiki_count} complete Wiki knowledge bases from the pending signal cards provided below.

INSTRUCTIONS:
1. Review the following {len(cards[:20])} pending proactive signal candidates.
2. Select the {wiki_count} MOST interesting topics, prioritizing topics related to AI, LLMs, Agents, coding, or our recent focus areas.
3. For EACH selected topic:
   - Perform deep research using NotebookLM (the `nlm-skill`) and standard web search.
   - Synthesize a comprehensive, structured markdown report with the findings.
   - Use the Image Generation tool (`generate_image` or Gemeni 3 Pro Image) to generate an infographic summarizing the top insights. 
     Save the image to the designated folder: {wiki_artifacts_dir}/{today}_wiki_infographic_[TOPIC].png
   - Ingest ALL compile insights into the Universal Agent Wiki using the `wiki_ingest_external_source` tool.
4. When finished, format a "Nightly Wiki Report" documenting the selected topics, and absolute links to the NLM outputs, generated infographics, and the core themes added to the Wiki.
5. Save exactly one payload file named 'nightly_wiki_{today}.md' to {wiki_artifacts_dir} containing just this summary of links and results so the Morning Briefing agent can surface it to the user.

RAW PENDING CARDS:
```json
{cards_context}
```
"""
    
    logger.info(f"Dispatching nightly wiki mission to vp.general.primary for {wiki_count} wiki(s)...")
    
    from universal_agent.tools.vp_orchestration import _vp_dispatch_mission_impl
    
    result = await _vp_dispatch_mission_impl({
        "vp_id": "vp.general.primary",
        "objective": objective,
        "mission_type": "proactive_wiki",
        "idempotency_key": f"nightly-wiki-{today}",
        "execution_mode": "sdk",
    })
    
    if result.get("content", [{}])[0].get("text"):
        res_data = json.loads(result["content"][0]["text"])
        if res_data.get("ok"):
            logger.info(f"Successfully dispatched nightly wiki mission: {res_data.get('mission_id')}")
        else:
            logger.error(f"Failed to dispatch mission: {res_data}")
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
