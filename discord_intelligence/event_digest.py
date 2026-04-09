import os
import sqlite3
import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from universal_agent.services.llm_classifier import _call_llm
from discord_intelligence.config import get_db_path, init_secrets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] EVENT_DIGEST: %(message)s")
logger = logging.getLogger("event_digest")

BASE_DIR = Path(__file__).resolve().parent
DIGESTS_DIR = BASE_DIR / "digests"
DIGESTS_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT = """You are an event intelligence analyst.
Review the following Discord messages captured during a scheduled event.
Extract a concise summary, key insights, links shared, and any action items.
Format as Markdown with clear headings."""

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn

async def generate_digest(event_name: str, messages: list[dict]) -> Optional[str]:
    if not messages:
        return None
    
    text_content = "\n".join([f"[{m['timestamp']}] {m['author_name']}: {m['content']}" for m in messages])
    
    # We truncate text_content if it's extremely long, to fit within LLM context window limits
    max_chars = 150000 
    if len(text_content) > max_chars:
        text_content = text_content[:max_chars] + "\n...[truncated due to length]"
        
    user_msg = f"Event: {event_name}\nMessages:\n{text_content}"
    
    try:
        response = await _call_llm(system=SYSTEM_PROMPT, user=user_msg, max_tokens=1024)
        return response
    except Exception as e:
        logger.error(f"Failed to generate digest: {e}")
        return None

async def run_pipeline():
    logger.info("Starting Event Digest Pipeline")
    init_secrets()
    db = get_connection()
    try:
        now = datetime.now(timezone.utc)
        events = db.execute("SELECT * FROM scheduled_events").fetchall()
        
        for event in events:
            digest_path = DIGESTS_DIR / f"{event['id']}_digest.md"
            if digest_path.exists():
                continue
                
            start_iso = event['start_time']
            end_iso = event['end_time']
            if not start_iso:
                continue
                
            start_dt = datetime.fromisoformat(start_iso.replace('Z', '+00:00'))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            else:
                start_dt = start_dt.astimezone(timezone.utc)
                
            if start_dt > now:
                continue # hasn't started yet
                
            window_start = start_dt - timedelta(minutes=15)
            if end_iso:
                end_dt = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                else:
                    end_dt = end_dt.astimezone(timezone.utc)
                window_end = end_dt + timedelta(minutes=15)
            else:
                window_end = start_dt + timedelta(hours=2) # default to 2 hours
                
            # If the event is still ongoing, maybe we should wait.
            # We process if `now` is past `window_end`
            if now < window_end:
                continue
            
            # Query messages in this server within window
            sql = '''
                SELECT * FROM messages 
                WHERE server_id = ? 
                AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
            '''
            args = (event['server_id'], window_start.isoformat(), window_end.isoformat())
            msgs = db.execute(sql, args).fetchall()
            
            if msgs:
                logger.info(f"Extracting digest for event '{event['name']}' with {len(msgs)} messages.")
                digest = await generate_digest(event['name'], [dict(m) for m in msgs])
                if digest:
                    digest_path.write_text(digest)
                    logger.info(f"Saved local digest to {digest_path}")
                    
                    # Update LLM Wiki / Briefings integration
                    kb_path = Path("/home/kjdragan/lrepos/universal_agent/kb/briefings")
                    if not kb_path.exists():
                        kb_path.mkdir(parents=True, exist_ok=True)
                        
                    safe_name = "".join(c for c in event['name'] if c.isalnum() or c in " _-").replace(' ', '_')
                    briefing_file = kb_path / f"Event_{safe_name}.md"
                    
                    briefing_content = f"# Intelligence Briefing: {event['name']}\nDate: {start_dt.isoformat()}\n\n{digest}"
                    briefing_file.write_text(briefing_content)
                    logger.info(f"Pushed to KB Briefings: {briefing_file}")
                    
    finally:
        db.close()
    
    logger.info("Event Digest Pipeline completed.")

if __name__ == "__main__":
    asyncio.run(run_pipeline())
