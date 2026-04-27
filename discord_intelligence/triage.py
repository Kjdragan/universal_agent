from datetime import datetime
import json
import logging
import os
import re

from anthropic import AsyncAnthropic

from .config import CONFIG
from .database import DiscordIntelligenceDB
from universal_agent.rate_limiter import ZAIRateLimiter

logger = logging.getLogger(__name__)

TRIAGE_SYSTEM_PROMPT = """\
You are an expert tech ecosystem analyst and developer relations engineer analyzing Discord community messages.
Your goal is to extract broad educational themes, emerging macro trends, architectural insights, and valuable conceptual takeaways.

DO NOT log individual user complaints, niche debugging sessions, narrow framework-specific bugs, or minor feature requests unless they illustrate a larger, generalized industry trend or architectural anti-pattern. 
Instead, concentrate the knowledge into high-level, generalized topics for a CTO or Senior Architecht to consider - similar to high-quality educational YouTube content.

Output strictly as a JSON object with no additional commentary:
{
  "insights": [
    {
      "topic": "string (Educational, trend, or macro topic title)",
      "summary": "A detailed synthesis of the conceptual takeaway or generalized knowledge block.",
      "sentiment": "positive|neutral|negative",
      "urgency": "high|medium|low",
      "confidence": 0.9,
      "source_message_ids": ["id1", "id2"]
    }
  ]
}

IMPORTANT: Ensure all strings are properly escaped. Do not use unescaped backslashes or newlines inside JSON string values.
"""

# Regex to extract JSON object from LLM response that may have markdown wrappers
_JSON_EXTRACT = re.compile(r'\{[\s\S]*\}')


def _parse_llm_json(raw_text: str) -> dict | None:
    """Robustly parse JSON from an LLM response, handling common failure modes."""
    # Strip markdown code fences
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'```\s*$', '', text)
        text = text.strip()

    # Try standard parsing first (strict=False handles unescaped control chars)
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass

    # Try extracting the first JSON object via regex
    match = _JSON_EXTRACT.search(text)
    if match:
        try:
            return json.loads(match.group(0), strict=False)
        except json.JSONDecodeError:
            pass

    # Last resort: try to fix common escape issues
    try:
        # Replace unescaped backslashes that aren't valid escape sequences
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
        return json.loads(fixed, strict=False)
    except json.JSONDecodeError:
        pass

    return None


async def run_triage_batch(db: DiscordIntelligenceDB, channel_id: str, limit: int = 50):
    """
    Looks for unprocessed messages in a channel, runs LLM analysis to extract insights.
    """
    unprocessed = db.get_unprocessed_messages(channel_id, limit=max(1, int(limit)))
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
        data = _parse_llm_json(raw_text)

        if data is None:
            logger.error(f"Failed to parse JSON from triage LLM for {channel_id}. Raw (truncated): {raw_text[:200]}")
            db.create_triage_batch(channel_id, start_time, end_time, len(unprocessed), 'failed')
            # Still mark as processed so we don't re-attempt the same broken batch forever
            db.mark_messages_processed([m["id"] for m in unprocessed])
            return
        
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
