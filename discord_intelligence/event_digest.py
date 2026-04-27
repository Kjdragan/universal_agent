import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Optional

from discord_intelligence.config import get_db_path, init_secrets

from universal_agent.services.llm_classifier import _call_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] EVENT_DIGEST: %(message)s")
logger = logging.getLogger("event_digest")

BASE_DIR = Path(__file__).resolve().parent
APP_ROOT = BASE_DIR.parent
DIGESTS_DIR = BASE_DIR / "digests"
DIGESTS_DIR.mkdir(exist_ok=True)
BRIEFINGS_DIR = Path(os.getenv("UA_DISCORD_BRIEFINGS_DIR", str(APP_ROOT / "kb" / "briefings")))
AUTO_CREATE_DIGEST_ACTION_TASKS = str(os.getenv("UA_DISCORD_DIGEST_CREATE_TASKS", "0")).strip().lower() in {
    "1", "true", "yes", "on",
}

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
        
        for event_row in events:
            event = dict(event_row)
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
            msg_rows = [dict(m) for m in msgs]
            
            # Also check for audio transcript to incorporate
            transcript_text = ""
            transcript_path_val = event.get('transcript_path')
            if transcript_path_val and Path(transcript_path_val).exists():
                try:
                    transcript_text = Path(transcript_path_val).read_text(encoding="utf-8")
                    logger.info(f"Found transcript for event '{event['name']}', including in digest.")
                except Exception as e:
                    logger.warning(f"Could not read transcript {transcript_path_val}: {e}")
            
            total_chars = sum(len(str(m.get('content') or "")) for m in msg_rows)
            if len(msg_rows) < 10 and total_chars < 500 and not transcript_text:
                logger.info(f"Skipping event '{event['name']}' due to low message count ({len(msg_rows)}), low content length ({total_chars} chars), and no transcript.")
                continue

            if msg_rows or transcript_text:
                content_parts = []
                
                if msg_rows:
                    logger.info(f"Extracting digest for event '{event['name']}' with {len(msg_rows)} messages.")
                    content_parts.append(f"## Text Channel Messages ({len(msg_rows)} messages)")
                    
                if transcript_text:
                    content_parts.append(f"\n\n## Audio Transcript\n\n{transcript_text}")
                
                # Generate digest from all available content
                digest_data = await generate_digest(
                    event['name'],
                    msg_rows,
                    additional_context=transcript_text if transcript_text else None,
                )
                
                if digest_data:
                    digest_md = digest_data.get('summary_markdown', str(digest_data))
                    digest_path.write_text(digest_md)
                    logger.info(f"Saved local digest to {digest_path}")
                    
                    # Update LLM Wiki / Briefings integration
                    kb_path = BRIEFINGS_DIR
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
                    if action_items and AUTO_CREATE_DIGEST_ACTION_TASKS:
                        logger.info(f"Formulating AgentMail notice for {len(action_items)} action items rather than direct Tasks.")
                        body_lines = [f"Simone detected the following interesting action items in Discord (Event: {event['name']}):\n"]
                        for i, action in enumerate(action_items, 1):
                            body_lines.append(f"{i}. {action.get('title', 'Task')}")
                            body_lines.append(f"   {action.get('description', '')}\n")
                        
                        body_lines.append("\nShould I move forward with any of these? Reply to this email to let me know.")
                        body_content = "\n".join(body_lines)
                        
                        try:
                            from universal_agent.services.agentmail_service import (
                                AgentMailService,
                            )
                            svc = AgentMailService()
                            await svc.startup()
                            if svc._started:
                                target_email = os.getenv("UA_USER_EMAIL", "kevin.dragan@outlook.com")
                                await svc.send_email(
                                    to=target_email,
                                    subject=f"Discord Insight: Action Items from {event['name']}",
                                    text=body_content,
                                    require_approval=False  # Actually send the email to the user so they can reply
                                )
                                logger.info(f"Sent email notice to {target_email}")
                            else:
                                logger.warning("AgentMailService failed to start, cannot send email notice.")
                            await svc.shutdown()
                        except Exception as e:
                            logger.error(f"Failed to send AgentMail notice: {e}")
                    elif action_items:
                        logger.info(
                            "Digest generated %d action item(s) without AgentMail promotion "
                            "(set UA_DISCORD_DIGEST_CREATE_TASKS=1 to enable).",
                            len(action_items),
                        )

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
