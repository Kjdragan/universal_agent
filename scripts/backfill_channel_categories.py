#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path

# Try to import our LLM classifier wrapper
try:
    from universal_agent.infisical_loader import initialize_runtime_secrets
    initialize_runtime_secrets()
    from universal_agent.services.llm_classifier import _call_llm, _parse_json_response
except ImportError:
    import sys
    sys.path.append("/home/kjdragan/lrepos/universal_agent/src")
    from universal_agent.infisical_loader import initialize_runtime_secrets
    initialize_runtime_secrets()
    from universal_agent.services.llm_classifier import _call_llm, _parse_json_response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WATCHLIST_PATH = Path(os.getenv("CSI_YOUTUBE_WATCHLIST_FILE", "/home/kjdragan/lrepos/universal_agent/channels_watchlist.json")).expanduser()
DB_PATH = Path(os.getenv("CSI_DB_PATH", "/home/kjdragan/lrepos/universal_agent/CSI_Ingester/development/var/csi.db")).expanduser()

SYSTEM_PROMPT = """You are a taxonomy classifier for YouTube channels. You will be given either a sample of recent video summaries/transcripts from a channel, OR the channel's metadata. 
You must categorize the channel into EXACTLY ONE of the following 10 categories:
1. ai_coding_and_agents
2. ai_models_and_research
3. ai_news_and_business
4. software_engineering
5. geopolitics_and_conflict
6. longform_interviews
7. cooking
8. personal_health
9. other_signal
10. noise

Respond with a JSON object containing a single key "category" with the exact string from the list above."""

async def classify_channel(channel_name: str, descriptions: list[str]) -> str:
    user_content = json.dumps({
        "channel_name": channel_name,
        "content_samples": descriptions
    })
    
    try:
        raw = await _call_llm(system=SYSTEM_PROMPT, user=user_content, max_tokens=150)
        parsed = _parse_json_response(raw)
        cat = parsed.get("category", "other_signal")
        return cat
    except Exception as e:
        logger.error(f"Failed to classify {channel_name}: {e}")
        return "other_signal"

async def main():
    if not WATCHLIST_PATH.exists():
        logger.error(f"Watchlist not found at {WATCHLIST_PATH}")
        return
        
    with open(WATCHLIST_PATH, "r") as f:
        data = json.load(f)
        
    channels = data.get("channels", [])
    if not channels:
        logger.info("No channels found.")
        return

    logger.info(f"Found {len(channels)} channels to backfill.")
    
    # Pre-fetch all available transcripts/summaries to avoid hitting the DB in a loop
    channel_content_map = {}
    if DB_PATH.exists():
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                # Use summary_text if available, fallback to title. We don't want massive transcripts.
                cursor.execute("""
                    SELECT channel_name, title, summary_text 
                    FROM rss_event_analysis 
                    ORDER BY published_at DESC 
                    LIMIT 2000
                """)
                for row in cursor.fetchall():
                    cname, title, summary = row
                    if not cname: continue
                    if cname not in channel_content_map:
                        channel_content_map[cname] = []
                    if len(channel_content_map[cname]) < 5:
                        channel_content_map[cname].append(f"Title: {title}\nSummary: {summary[:500] if summary else 'N/A'}")
        except Exception as e:
            logger.warning(f"Could not read from DB: {e}")
    else:
        logger.warning(f"DB not found at {DB_PATH}")

    stats = {"transcript": 0, "metadata": 0, "failed": 0}
    
    # Process channels
    for i, ch in enumerate(channels):
        if not isinstance(ch, dict): continue
        
        cname = ch.get("channel_name") or ch.get("title") or "Unknown"
        logger.info(f"[{i+1}/{len(channels)}] Categorizing: {cname}")
        
        content = channel_content_map.get(cname, [])
        method = "transcript" if content else "metadata"
        
        if not content:
            # Fallback to metadata
            desc = ch.get("description", "No description available.")
            content = [f"Channel Description: {desc}"]
            
        category = await classify_channel(cname, content)
        logger.info(f" -> Assigned: {category} (via {method})")
        
        ch["domain"] = category
        ch["_categorization_method"] = method
        stats[method] += 1
        
        # Save progress every 10 channels
        if i % 10 == 0:
            with open(WATCHLIST_PATH, "w") as f:
                json.dump(data, f, indent=2)
                
    # Final save
    with open(WATCHLIST_PATH, "w") as f:
        json.dump(data, f, indent=2)
        
    logger.info("Backfill complete!")
    logger.info(f"Stats: {stats}")

if __name__ == "__main__":
    asyncio.run(main())
