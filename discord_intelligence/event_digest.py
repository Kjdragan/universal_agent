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
Review the following Discord messages and/or audio transcript captured during a scheduled event.
Extract a concise summary, key insights, links shared, and any explicit action items or tasks assigned.
Respond with ONLY a JSON object:
{
  "summary_markdown": "Formatted Markdown with ## Summary, ## Key Insights, and ## Links",
  "action_items": [
    {
      "title": "Short action title",
      "description": "Details of the task"
    }
  ]
}"""

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn

async def generate_digest(event_name: str, messages: list[dict], additional_context: str = None) -> Optional[str]:
    if not messages and not additional_context:
        return None
    
    text_content = ""
    if messages:
        text_content = "\n".join([f"[{m['timestamp']}] {m['author_name']}: {m['content']}" for m in messages])
    
    # We truncate text_content if it's extremely long, to fit within LLM context window limits
    max_chars = 150000 
    if len(text_content) > max_chars:
        text_content = text_content[:max_chars] + "\n...[truncated due to length]"
    
    # Include audio transcript if available
    transcript_section = ""
    if additional_context:
        max_transcript = 100000
        if len(additional_context) > max_transcript:
            additional_context = additional_context[:max_transcript] + "\n...[transcript truncated]"
        transcript_section = f"\n\nAudio Transcript:\n{additional_context}"
        
    user_msg = f"Event: {event_name}\nMessages:\n{text_content}{transcript_section}"
    
    try:
        response = await _call_llm(system=SYSTEM_PROMPT, user=user_msg, model="sonnet", max_tokens=2048)
        from universal_agent.services.llm_classifier import _parse_json_response
        parsed = _parse_json_response(response)
        return parsed
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
            
            # Also check for audio transcript to incorporate
            transcript_text = ""
            transcript_path_val = event.get('transcript_path')
            if transcript_path_val and Path(transcript_path_val).exists():
                try:
                    transcript_text = Path(transcript_path_val).read_text(encoding="utf-8")
                    logger.info(f"Found transcript for event '{event['name']}', including in digest.")
                except Exception as e:
                    logger.warning(f"Could not read transcript {transcript_path_val}: {e}")
            
            total_chars = sum(len(m['content']) for m in msgs if m.get('content'))
            if len(msgs) < 10 and total_chars < 500 and not transcript_text:
                logger.info(f"Skipping event '{event['name']}' due to low message count ({len(msgs)}) and low content length ({total_chars} chars), and no transcript.")
                continue

            if msgs or transcript_text:
                content_parts = []
                
                if msgs:
                    logger.info(f"Extracting digest for event '{event['name']}' with {len(msgs)} messages.")
                    content_parts.append(f"## Text Channel Messages ({len(msgs)} messages)")
                    
                if transcript_text:
                    content_parts.append(f"\n\n## Audio Transcript\n\n{transcript_text}")
                
                # Generate digest from all available content
                combined_messages = [dict(m) for m in msgs] if msgs else []
                digest_data = await generate_digest(
                    event['name'],
                    combined_messages,
                    additional_context=transcript_text if transcript_text else None,
                )
                
                if digest_data:
                    digest_md = digest_data.get('summary_markdown', str(digest_data))
                    digest_path.write_text(digest_md)
                    logger.info(f"Saved local digest to {digest_path}")
                    
                    # Update LLM Wiki / Briefings integration
                    kb_path = Path("/home/kjdragan/lrepos/universal_agent/kb/briefings")
                    if not kb_path.exists():
                        kb_path.mkdir(parents=True, exist_ok=True)
                        
                    safe_name = "".join(c for c in event['name'] if c.isalnum() or c in " _-").replace(' ', '_')
                    briefing_file = kb_path / f"Event_{safe_name}.md"
                    
                    # Include source indicator
                    sources = []
                    if msgs:
                        sources.append(f"{len(msgs)} text messages")
                    if transcript_text:
                        sources.append("audio transcript")
                    source_line = f"Sources: {', '.join(sources)}"
                    
                    briefing_content = f"# Intelligence Briefing: {event['name']}\nDate: {start_dt.isoformat()}\n{source_line}\n\n{digest_md}"
                    briefing_file.write_text(briefing_content)
                    logger.info(f"Pushed to KB Briefings: {briefing_file}")
                    
                    db.execute('''
                        INSERT OR IGNORE INTO knowledge_updates (id, title, summary, file_path)
                        VALUES (?, ?, ?, ?)
                    ''', (f"evt_{event['id']}", event['name'], digest_md[:2000], str(briefing_file)))
                    
                    action_items = digest_data.get('action_items', [])
                    if action_items:
                        from discord_intelligence.integration.task_hub import create_task_hub_mission
                        for action in action_items:
                            create_task_hub_mission(
                                title=f"Action Item: {action.get('title', 'Task')} (from {event['name']})",
                                description=action.get('description', ''),
                                tags=["event-action-item", "discord"]
                            )
                            logger.info(f"Created Task Hub mission for: {action.get('title', 'Task')}")

                    db.execute('''
                        UPDATE scheduled_events 
                        SET digest_generated = 1, digest_content = ? 
                        WHERE id = ?
                    ''', (digest_md, event['id']))

                    db.commit()
                    
    finally:
        db.close()
    
    logger.info("Event Digest Pipeline completed.")

if __name__ == "__main__":
    asyncio.run(run_pipeline())

