"""
proactive_topic_tracker.py — Prevent heartbeat from repeating the same proactive investigations.

When the heartbeat has no Task Hub claims and enters proactive mode, the LLM
independently decides what to investigate using the static checklist in HEARTBEAT.md.
Without memory of past runs, it gravitates to the same high-priority items every cycle.

This module provides:
  1. extract_topic_fingerprint()  — deterministic signature from response text
  2. record_topic()              — append entry to HeartbeatState.recent_topics
  3. format_recent_topics_prompt()— format history as a prompt section so the LLM
                                    avoids repeating itself

All logic is pure Python (no LLM calls).  The topic list is stored inside the
existing heartbeat_state.json — no new DB tables or files needed.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_MAX_RECENT_TOPICS = 12       # Keep at most this many entries
DEFAULT_TOPIC_EXPIRY_SECONDS = 86400  # 24 hours — entries older than this are pruned
DEFAULT_SUMMARY_MAX_CHARS = 300       # Max chars stored per topic summary


# ---------------------------------------------------------------------------
# Topic Fingerprint Extraction
# ---------------------------------------------------------------------------

def extract_topic_fingerprint(response_text: str) -> str:
    """Produce a short deterministic fingerprint from the heartbeat response.

    Strategy: normalise the first ~500 chars of the response (lowercase, collapse
    whitespace, strip markdown), then SHA-256 hash.  Two responses about "AI Model
    Releases" will produce the same fingerprint even if wording differs slightly,
    because the dominant keywords in the opening paragraph will match.

    For more robust similarity we extract keywords first.
    """
    if not response_text:
        return ""
    # Normalise: strip markdown formatting, collapse whitespace, lowercase
    cleaned = re.sub(r"[#*_`>\[\]\(\)!|]", " ", response_text[:800])
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    # Extract top keywords (words ≥4 chars, deduplicated, sorted for stability)
    words = re.findall(r"[a-z]{4,}", cleaned)
    # Take up to 30 unique keywords to form the fingerprint basis
    seen: set[str] = set()
    keywords: list[str] = []
    for w in words:
        if w not in seen and w not in _STOP_WORDS:
            seen.add(w)
            keywords.append(w)
            if len(keywords) >= 30:
                break
    keyword_text = " ".join(sorted(keywords))
    return hashlib.sha256(keyword_text.encode()).hexdigest()[:16]


def _extract_topic_title(response_text: str) -> str:
    """Extract a human-readable topic title from the response.

    Looks for the first markdown heading, or falls back to the first sentence.
    """
    if not response_text:
        return "(unknown topic)"
    # Try to find a markdown heading
    match = re.search(r"^#+\s+(.+)$", response_text, re.MULTILINE)
    if match:
        title = match.group(1).strip()
        if len(title) > 10:  # skip trivially short headings
            return title[:120]
    # Fall back to first substantial sentence
    sentences = re.split(r"[.!?\n]", response_text[:500])
    for s in sentences:
        s = s.strip()
        if len(s) > 20:
            return s[:120]
    return response_text[:80].strip()


# Common English stop words to exclude from fingerprinting
_STOP_WORDS = frozenset({
    "that", "this", "with", "from", "have", "been", "will", "would", "could",
    "should", "their", "there", "they", "them", "then", "than", "these",
    "those", "were", "what", "when", "where", "which", "while", "your",
    "about", "after", "also", "because", "before", "between", "both",
    "each", "even", "every", "first", "here", "into", "just", "like",
    "make", "many", "more", "most", "much", "must", "need", "only",
    "other", "over", "same", "some", "such", "take", "through", "under",
    "very", "well", "work", "does", "done", "found", "going", "know",
    "look", "made", "next", "part", "right", "said", "show", "still",
    "sure", "tell", "time", "want", "back", "best", "came", "come",
    "good", "help", "keep", "last", "long", "note", "open", "read",
    "report", "report", "check", "system", "status", "agent", "heartbeat",
    "following", "current", "monitoring", "review", "results", "summary",
})


# ---------------------------------------------------------------------------
# Recording & Expiry
# ---------------------------------------------------------------------------

def record_topic(
    state: Any,
    *,
    topic_summary: str,
    fingerprint: str,
    max_entries: int = DEFAULT_MAX_RECENT_TOPICS,
    expiry_seconds: int = DEFAULT_TOPIC_EXPIRY_SECONDS,
) -> None:
    """Append a topic entry to state.recent_topics, pruning old/duplicate entries.

    ``state`` is a HeartbeatState dataclass instance (must have ``recent_topics``
    attribute — a list of dicts).
    """
    if not fingerprint:
        return

    now = time.time()
    topics: list[dict[str, Any]] = list(getattr(state, "recent_topics", None) or [])

    # 1. Prune expired entries
    cutoff = now - expiry_seconds
    topics = [t for t in topics if (t.get("timestamp") or 0) > cutoff]

    # 2. Deduplicate: if this fingerprint already exists, update timestamp
    existing_fps = {t.get("fingerprint") for t in topics}
    if fingerprint in existing_fps:
        for t in topics:
            if t.get("fingerprint") == fingerprint:
                t["timestamp"] = now
                t["hit_count"] = int(t.get("hit_count") or 1) + 1
                break
    else:
        title = _extract_topic_title(topic_summary)
        topics.append({
            "fingerprint": fingerprint,
            "title": title,
            "summary": topic_summary[:DEFAULT_SUMMARY_MAX_CHARS],
            "timestamp": now,
            "hit_count": 1,
        })

    # 3. Cap to max entries (keep most recent)
    if len(topics) > max_entries:
        topics.sort(key=lambda t: t.get("timestamp") or 0, reverse=True)
        topics = topics[:max_entries]

    state.recent_topics = topics


# ---------------------------------------------------------------------------
# Prompt Formatting
# ---------------------------------------------------------------------------

def format_recent_topics_prompt(
    state: Any,
    *,
    expiry_seconds: int = DEFAULT_TOPIC_EXPIRY_SECONDS,
) -> str:
    """Format recent topics into a prompt section for the heartbeat LLM.

    Returns empty string if there are no recent topics.
    """
    topics: list[dict[str, Any]] = list(getattr(state, "recent_topics", None) or [])
    if not topics:
        return ""

    now = time.time()
    cutoff = now - expiry_seconds
    active_topics = [t for t in topics if (t.get("timestamp") or 0) > cutoff]

    if not active_topics:
        return ""

    # Sort by recency (most recent first)
    active_topics.sort(key=lambda t: t.get("timestamp") or 0, reverse=True)

    lines = [
        "== RECENT INVESTIGATIONS (do NOT repeat these topics) ==",
        "The following topics were already investigated in recent heartbeat cycles.",
        "You MUST choose a DIFFERENT topic or angle for this cycle.\n",
    ]
    for idx, topic in enumerate(active_topics, 1):
        title = str(topic.get("title") or "(unknown)").strip()
        age_hours = (now - (topic.get("timestamp") or now)) / 3600
        hit_count = int(topic.get("hit_count") or 1)
        repeat_note = f" (repeated {hit_count}x)" if hit_count > 1 else ""
        lines.append(f"  {idx}. {title} — {age_hours:.1f}h ago{repeat_note}")

    lines.append("")
    lines.append(
        "INSTRUCTION: Pick a topic from your Active Monitors list that is NOT in the above list, "
        "or find a genuinely new angle on an existing topic. If all items have been recently "
        "covered, focus on operational hygiene, advancing brainstorm tasks, or report HEARTBEAT_OK."
    )
    return "\n".join(lines)
