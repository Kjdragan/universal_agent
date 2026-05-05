import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
import sys

from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.proactive_signals import CARD_STATUS_PENDING, list_cards

logger = logging.getLogger(__name__)

async def main():
    # 1. Initialize runtime secrets via Infisical
    initialize_runtime_secrets(profile="local_workstation")
    logging.basicConfig(level=logging.INFO)
    
    # Ensure artifacts directory for wikis exists — use the canonical resolver
    # that works on both desktop and VPS (resolves relative to repo root).
    from universal_agent.artifacts import resolve_artifacts_dir
    artifacts_dir = str(resolve_artifacts_dir())
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
3. For EACH selected topic, follow the NLM-FIRST pipeline:
   a. Create a NotebookLM notebook: `nlm notebook create "Topic Title"`
   b. Run NLM research: `nlm research start "topic query" --notebook-id <id>` (use fast mode by default)
   c. Poll: `nlm research status <id> --max-wait 0` with adaptive sleep intervals (sleep 5 for fast, sleep 20 for deep) until completed
   d. Import sources: `nlm research import <id> <task-id>`
   e. Generate artifacts via NLM studio — fire ALL creates first, then poll once:
      - `nlm report create <id> --confirm`
      - `nlm infographic create <id> --orientation landscape --style professional --confirm`
   f. Poll `nlm studio status <id>` with sleep 10 until all artifacts are completed
   g. Download artifacts:
      - `nlm download report <id> --output {wiki_artifacts_dir}/{today}_wiki_report_[TOPIC].md`
      - `nlm download infographic <id> --output {wiki_artifacts_dir}/{today}_wiki_infographic_[TOPIC].png`
   h. Register the KB: use the `kb_register` tool with slug, notebook_id, title, and tags
   i. Ingest the report into the Wiki using `wiki_ingest_external_source`

   IMPORTANT: Do NOT use `generate_image` or HTML-to-PDF for infographics.
   NLM studio generates source-grounded infographics that are higher quality.

4. When finished, format a "Nightly Wiki Report" documenting the selected topics, notebook URLs, absolute paths to downloaded artifacts, and the core themes added to the Wiki.
5. Save exactly one payload file named 'nightly_wiki_{today}.md' to {wiki_artifacts_dir} containing just this summary so the Morning Briefing agent can surface it to the user.

RAW PENDING CARDS:
```json
{cards_context}
```
"""
    
    logger.info(f"Dispatching nightly wiki mission to vp.general.primary for {wiki_count} wiki(s)...")

    from universal_agent.tools.vp_orchestration import dispatch_vp_mission

    try:
        await dispatch_vp_mission(
            objective=objective,
            mission_type="proactive_wiki",
            idempotency_key=f"nightly-wiki-{today}",
        )
    except RuntimeError as exc:
        logger.error(f"Dispatch failed: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
