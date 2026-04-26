"""
LLM-based relevance filter for Discord CSI messages.

Classifies messages as meaningful signal vs noise using a cheap, fast model
(Haiku-class). Runs as a periodic sweep rather than inline in on_message
to avoid backpressure on the Discord gateway.

Design:
  - Cross-channel batching: messages from all servers go into one LLM call
  - Server/channel context is embedded inline per message
  - 2 concurrent workers for large backlogs
  - Store-but-hide: noise messages stay in DB with is_meaningful=false
"""

import asyncio
import json
import os
import re
import logging
from anthropic import AsyncAnthropic
from universal_agent.rate_limiter import ZAIRateLimiter
from .config import CONFIG
from .database import DiscordIntelligenceDB

logger = logging.getLogger(__name__)

RELEVANCE_SYSTEM_PROMPT = """\
You are a signal-vs-noise filter for a Discord intelligence monitoring dashboard.
Your job is to classify each message as MEANINGFUL (worth showing) or NOISE (hide).

A message is MEANINGFUL if it contains ANY of:
- Product announcements, releases, version updates, or changelogs
- Architectural decisions, design discussions, or technical deep-dives
- Breaking changes, deprecations, or migration guidance
- Event/webinar/AMA/office-hours announcements
- Substantive technical insights, emerging patterns, or trend shifts
- Official team communications, policy changes, or roadmap updates
- Notable community contributions or showcase projects
- Security advisories, CVEs, or vulnerability disclosures

A message is NOISE if it is:
- Casual chat, greetings, social banter, or small talk
- Basic support questions ("how do I install X?", "getting an error with...")
- One-word responses, emoji-only messages, or reactions
- Debugging sessions, stack traces, or error log pastes
- Off-topic conversation unrelated to the project
- Bot-generated routine messages (join/leave, role assignments)
- Simple "thank you" or acknowledgment messages

Output ONLY valid JSON with no extra text:
{"results": [{"id": 1, "meaningful": true}, {"id": 2, "meaningful": false}, ...]}

Classify EVERY message in the batch. When in doubt, lean toward marking as meaningful.
"""

# Regex to extract JSON object from LLM response that may have markdown wrappers
_JSON_EXTRACT = re.compile(r'\{[\s\S]*\}')


def _parse_llm_json(raw_text: str) -> dict | None:
    """Robustly parse JSON from an LLM response, handling common failure modes."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'```\s*$', '', text)
        text = text.strip()

    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass

    match = _JSON_EXTRACT.search(text)
    if match:
        try:
            return json.loads(match.group(0), strict=False)
        except json.JSONDecodeError:
            pass

    try:
        fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
        return json.loads(fixed, strict=False)
    except json.JSONDecodeError:
        pass

    return None


def _format_batch_prompt(messages: list[dict]) -> str:
    """Format a batch of messages into a numbered list for the LLM.
    
    Each message includes inline server/channel context so the LLM
    can make domain-aware decisions in a single cross-channel call.
    """
    lines = []
    for i, msg in enumerate(messages, 1):
        server = msg.get("server_name") or "Unknown Server"
        channel = msg.get("channel_name") or "unknown"
        author = msg.get("author_name") or "unknown"
        content = (msg.get("content") or "").strip()
        
        # Truncate very long messages to save tokens
        if len(content) > 500:
            content = content[:500] + "..."
        
        # Skip empty messages — mark as noise directly
        if not content:
            continue
        
        bot_tag = " [BOT]" if msg.get("is_bot") else ""
        lines.append(f"[{i}] ({server} / #{channel}) @{author}{bot_tag}: {content}")
    
    return "Classify these messages:\n" + "\n".join(lines)


async def classify_batch(
    messages: list[dict],
    model: str | None = None,
) -> list[tuple[str, bool]]:
    """Classify a batch of messages as meaningful or noise via LLM.
    
    Args:
        messages: List of message dicts with id, content, server_name, channel_name, etc.
        model: Model to use (defaults to config.yaml → models.relevance)
    
    Returns:
        List of (message_id, is_meaningful) tuples
    """
    if not messages:
        return []
    
    model = model or CONFIG.get("models", {}).get("relevance", "glm-4.5-air")
    limiter = ZAIRateLimiter.get_instance()
    
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    
    if not api_key:
        logger.error("No API key available for relevance filter.")
        return []
    
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    
    client = AsyncAnthropic(**client_kwargs)
    
    # Build index mapping: position (1-based) → message_id
    # Some messages with empty content are skipped in the prompt
    idx_to_id: dict[int, str] = {}
    for i, msg in enumerate(messages, 1):
        idx_to_id[i] = msg["id"]
    
    prompt = _format_batch_prompt(messages)
    
    # Messages with empty content get marked as noise directly
    results: list[tuple[str, bool]] = []
    for msg in messages:
        if not (msg.get("content") or "").strip():
            results.append((msg["id"], False))
    
    try:
        async with limiter.acquire(context="relevance_filter"):
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                system=RELEVANCE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
        
        raw_text = response.content[0].text.strip()
        data = _parse_llm_json(raw_text)
        
        if data is None:
            logger.error(
                "Failed to parse JSON from relevance filter LLM. "
                "Raw (truncated): %s", raw_text[:300]
            )
            # On parse failure, mark all as meaningful (fail-open)
            for msg in messages:
                if (msg.get("content") or "").strip():
                    results.append((msg["id"], True))
            return results
        
        # Parse LLM results
        llm_results = data.get("results", [])
        classified_ids = set()
        for item in llm_results:
            idx = item.get("id")
            meaningful = item.get("meaningful", True)
            if idx is not None and idx in idx_to_id:
                msg_id = idx_to_id[idx]
                if msg_id not in {r[0] for r in results}:  # Don't double-add
                    results.append((msg_id, bool(meaningful)))
                    classified_ids.add(idx)
        
        # Any messages the LLM missed → fail-open (mark meaningful)
        for idx, msg_id in idx_to_id.items():
            if idx not in classified_ids and msg_id not in {r[0] for r in results}:
                results.append((msg_id, True))
                logger.debug("LLM missed message idx=%d, marking as meaningful (fail-open)", idx)
        
        meaningful_count = sum(1 for _, m in results if m)
        noise_count = sum(1 for _, m in results if not m)
        logger.info(
            "Relevance batch classified: %d meaningful, %d noise (out of %d)",
            meaningful_count, noise_count, len(messages)
        )
        
    except Exception as e:
        logger.error("Relevance filter LLM call failed: %s", e)
        # Fail-open: mark all as meaningful so nothing is hidden
        for msg in messages:
            if msg["id"] not in {r[0] for r in results}:
                results.append((msg["id"], True))
    
    return results


async def run_relevance_sweep(
    db: DiscordIntelligenceDB,
    max_batch_size: int = 50,
    max_workers: int = 2,
):
    """Orchestrate a relevance filter sweep across all unfiltered messages.
    
    1. Fetch up to max_batch_size * max_workers unfiltered messages
    2. Split into sub-batches
    3. Run concurrent classify_batch calls
    4. Write results back to DB
    
    Returns:
        dict with counts: {"processed": N, "meaningful": M, "noise": K}
    """
    total_limit = max_batch_size * max_workers
    unfiltered = db.get_unfiltered_messages(limit=total_limit)
    
    if not unfiltered:
        return {"processed": 0, "meaningful": 0, "noise": 0}
    
    logger.info("Relevance sweep: %d unfiltered messages to classify", len(unfiltered))
    
    # Split into sub-batches for concurrent processing
    batches = []
    for i in range(0, len(unfiltered), max_batch_size):
        batch = unfiltered[i:i + max_batch_size]
        if batch:
            batches.append(batch)
    
    # Run concurrent workers
    if len(batches) == 1:
        all_results = await classify_batch(batches[0])
    else:
        tasks = [classify_batch(batch) for batch in batches]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        all_results = []
        for result in batch_results:
            if isinstance(result, Exception):
                logger.error("Batch classification failed: %s", result)
                # Fail-open for failed batches
                continue
            all_results.extend(result)
    
    # Write results to DB
    if all_results:
        db.mark_messages_meaningful(all_results)
    
    meaningful_count = sum(1 for _, m in all_results if m)
    noise_count = sum(1 for _, m in all_results if not m)
    
    logger.info(
        "Relevance sweep complete: %d processed, %d meaningful, %d noise",
        len(all_results), meaningful_count, noise_count
    )
    
    return {
        "processed": len(all_results),
        "meaningful": meaningful_count,
        "noise": noise_count,
    }
