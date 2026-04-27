import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

import anthropic

sys.path.append("/opt/universal_agent/src")
from universal_agent.infisical_loader import initialize_runtime_secrets

# --- Configuration ---
DB_PATH = Path("/var/lib/universal-agent/csi/csi.db") # VPS path
WATCHLIST_PATH = Path("/opt/universal_agent/CSI_Ingester/development/channels_watchlist.json") # Adjust if needed
MAX_TRANSCRIPTS = 5

# Initialize secrets from Infisical
initialize_runtime_secrets()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def analyze_channels():
    if not DB_PATH.exists():
        logger.error(f"Database not found at {DB_PATH}. Are you running this on the VPS?")
        return

    # Load watchlist
    if not WATCHLIST_PATH.exists():
        logger.error(f"Watchlist not found at {WATCHLIST_PATH}")
        return
        
    with open(WATCHLIST_PATH, "r") as f:
        watchlist = json.load(f)
        
    channels = watchlist.get("channels", [])
    logger.info(f"Loaded {len(channels)} channels from watchlist.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    client = anthropic.Anthropic()

    stats = {"transcript_based": 0, "metadata_based": 0, "failed": 0}

    for channel in channels:
        channel_id = channel.get("channel_id")
        channel_name = channel.get("channel_name", "Unknown")
        
        # 1. Fetch up to 5 transcripts/schemas from the DB
        cursor.execute('''
            SELECT title, summary_text, analysis_json 
            FROM rss_event_analysis 
            WHERE channel_id = ? AND transcript_status = 'present'
            ORDER BY analyzed_at DESC LIMIT ?
        ''', (channel_id, MAX_TRANSCRIPTS))
        
        rows = cursor.fetchall()
        
        prompt_content = ""
        used_transcripts = False
        
        if rows:
            used_transcripts = True
            prompt_content = f"Channel Name: {channel_name}\nRecent Videos:\n"
            for row in rows:
                prompt_content += f"- Title: {row['title']}\n"
                prompt_content += f"  Summary: {row['summary_text'][:500]}...\n"
        else:
            # Fallback to metadata
            prompt_content = f"Channel Name: {channel_name}\n"
            prompt_content += f"No recent videos available. Categorize based on the channel name and typical content for this name.\n"

        prompt = f"""
        Categorize the following YouTube channel into exactly ONE of the following categories:
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
        
        Here is the channel data:
        {prompt_content}
        
        Respond ONLY with a JSON object in this exact format:
        {{"category": "the_category_string", "rationale": "a short explanation"}}
        """

        try:
            response = client.messages.create(
                model="claude-3-5-sonnet-latest",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # naive json parsing
            import re
            text = response.content[0].text
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if not json_match:
                raise ValueError("No JSON found in response")
            result = json.loads(json_match.group(0))
            category = result.get("category")
            
            # Update the DB
            cursor.execute("UPDATE youtube_channels SET domain = ? WHERE channel_id = ?", (category, channel_id))
            conn.commit()
            
            if used_transcripts:
                stats["transcript_based"] += 1
                logger.info(f"Categorized [TRANSCRIPT]: {channel_name} -> {category}")
            else:
                stats["metadata_based"] += 1
                logger.info(f"Categorized [METADATA]: {channel_name} -> {category}")

        except Exception as e:
            stats["failed"] += 1
            logger.error(f"Failed to categorize {channel_name}: {e}")

    logger.info("Categorization Complete!")
    logger.info(f"Stats: {stats}")

if __name__ == "__main__":
    analyze_channels()
