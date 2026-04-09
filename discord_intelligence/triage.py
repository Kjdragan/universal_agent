import os
import json
import logging
from datetime import datetime
from anthropic import AsyncAnthropic
from universal_agent.rate_limiter import ZAIRateLimiter
from .config import CONFIG
from .database import DiscordIntelligenceDB

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM_PROMPT = """\
You are an expert developer relations engineer analyzing Discord community messages.
Extract actionable insights, recurring issues, bugs, feature requests, or general community sentiment from this batch of messages.

Output strictly as a JSON object:
{
  "insights": [
    {
      "topic": "string",
      "summary": "Detailed summary of the finding.",
      "sentiment": "positive|neutral|negative",
      "urgency": "high|medium|low",
      "confidence": 0.9,
      "source_message_ids": ["id1", "id2"]
    }
  ]
}
"""

async def run_triage_batch(db: DiscordIntelligenceDB, channel_id: str):
    """
    Looks for unprocessed messages in a channel, runs LLM analysis to extract insights.
    """
    unprocessed = db.get_unprocessed_messages(channel_id, limit=200)
    if not unprocessed:
        return
        
    start_time = datetime.fromisoformat(unprocessed[0]['timestamp'])
    end_time = datetime.fromisoformat(unprocessed[-1]['timestamp'])
    
    # We construct a formatted text for LLM
    text_lines = []
    for m in unprocessed:
        text_lines.append(f"[{m['id']}] {m['author_name']}: {m['content']}")
    
    prompt = "Analyze these messages:\n" + "\n".join(text_lines)

    limiter = ZAIRateLimiter.get_instance()
    
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    
    if not api_key:
        logger.error("No API key available for triage.")
        return
        
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = AsyncAnthropic(**client_kwargs)

    try:
        async with limiter.acquire():
            response = await client.messages.create(
                model=CONFIG.get("models", {}).get("triage", "claude-haiku-4-5-20251001"),
                max_tokens=2048,
                system=TRIAGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )
        
        raw_text = response.content[0].text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text.replace("```json", "", 1).rstrip("```").strip()
            
        data = json.loads(raw_text)
        
        # Create a batch record
        batch_id = db.create_triage_batch(channel_id, start_time, end_time, len(unprocessed), 'success')
        
        # Store insights
        for insight in data.get("insights", []):
            db.store_insight(
                batch_id=batch_id,
                topic=insight.get("topic", "Unknown"),
                summary=insight.get("summary", ""),
                sentiment=insight.get("sentiment", "neutral"),
                urgency=insight.get("urgency", "low"),
                confidence=float(insight.get("confidence", 0.0)),
                source_ids=insight.get("source_message_ids", [])
            )
            
    except Exception as e:
        logger.error(f"Failed to triage batch for {channel_id}: {e}")
        db.create_triage_batch(channel_id, start_time, end_time, len(unprocessed), 'failed')
        return

    # Mark as processed
    db.mark_messages_processed([m["id"] for m in unprocessed])
