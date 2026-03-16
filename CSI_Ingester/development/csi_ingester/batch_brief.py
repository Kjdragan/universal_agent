"""Batched brief scheduler — collects events, summarises via LLM, emits one digest."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from csi_ingester.config import CSIConfig
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.store import events as event_store

logger = logging.getLogger(__name__)

# ── Gemini Flash summarisation ───────────────────────────────────────────

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_DEFAULT_MODEL = "gemini-3-flash-preview"

_SYSTEM_PROMPT = """\
You are a concise trend analyst.  Given a batch of creator-signal events
collected over the last few hours, produce a Markdown brief with:
1. A **title line** as an H1 heading — a specific, descriptive headline summarising the most notable signal (NOT the word "Headline" — use an actual summary, e.g. "# AI Hardware Race Intensifies as NPU Adoption Surges").
2. **By Source** — bullet list grouped by source (YouTube, Reddit, Threads, etc.) with 1-2 sentence summaries per event.
3. **Emerging Themes** — 2-3 bullet points on patterns or themes you notice (or "No strong themes" if none).
Keep it under 600 words.  Do NOT fabricate data — only summarise what is provided.
IMPORTANT: The first line MUST be a concrete, descriptive H1 heading — never use generic labels like "Headline" or "Summary".
"""


def _build_prompt(rows: list[dict[str, Any]]) -> str:
    """Build the user prompt from raw event rows."""
    lines: list[str] = [f"## Batch of {len(rows)} events\n"]
    for i, row in enumerate(rows, 1):
        source = row.get("source", "unknown")
        event_type = row.get("event_type", "")
        occurred = row.get("occurred_at", "")
        subject = row.get("subject", {})
        title = subject.get("title", subject.get("brief_title", ""))
        summary = subject.get("summary", subject.get("brief_summary", subject.get("description", "")))
        lines.append(f"{i}. [{source}] {event_type} — **{title}**")
        if summary:
            lines.append(f"   {summary[:300]}")
        lines.append(f"   _occurred: {occurred}_\n")
    return "\n".join(lines)


async def _call_gemini(api_key: str, prompt: str, *, model: str = _DEFAULT_MODEL, timeout: int = 60) -> str:
    """Call Gemini Flash and return the generated text."""
    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048},
    }
    url = f"{_GEMINI_BASE}/{model}:generateContent?key={api_key}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()
    # Extract text from Gemini response
    candidates = body.get("candidates", [])
    if not candidates:
        raise ValueError("Gemini returned no candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


def _fallback_brief(rows: list[dict[str, Any]]) -> str:
    """Plain-text fallback when LLM is unavailable."""
    lines = [f"# CSI Batch Brief — {len(rows)} events\n"]
    lines.append(f"_Generated at {_utc_now()} (LLM unavailable — plain summary)_\n")
    by_source: dict[str, list[str]] = {}
    for row in rows:
        source = row.get("source", "unknown")
        subject = row.get("subject", {})
        title = subject.get("title", subject.get("brief_title", "Untitled"))
        by_source.setdefault(source, []).append(str(title))
    for source, titles in sorted(by_source.items()):
        lines.append(f"## {source} ({len(titles)} events)")
        for t in titles[:10]:
            lines.append(f"- {t}")
        if len(titles) > 10:
            lines.append(f"- _…and {len(titles) - 10} more_")
        lines.append("")
    return "\n".join(lines)


# ── Event collection from SQLite ─────────────────────────────────────────

def fetch_undelivered_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Fetch events with delivered=0, returning parsed dicts."""
    cursor = conn.execute(
        """
        SELECT event_id, source, event_type, occurred_at, received_at,
               subject_json, routing_json, metadata_json
        FROM events
        WHERE delivered = 0
        ORDER BY occurred_at ASC
        LIMIT 500
        """
    )
    results: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        try:
            subject = json.loads(row["subject_json"]) if row["subject_json"] else {}
        except (json.JSONDecodeError, TypeError):
            subject = {}
        results.append({
            "event_id": row["event_id"],
            "source": row["source"],
            "event_type": row["event_type"],
            "occurred_at": row["occurred_at"],
            "received_at": row["received_at"],
            "subject": subject,
        })
    return results


def mark_events_delivered(conn: sqlite3.Connection, event_ids: list[str]) -> int:
    """Batch-mark events as delivered."""
    if not event_ids:
        return 0
    now = _utc_now()
    count = 0
    for eid in event_ids:
        conn.execute(
            "UPDATE events SET delivered = 1, emitted_at = ? WHERE event_id = ?",
            (now, eid),
        )
        count += 1
    conn.commit()
    return count


# ── The main batch cycle ─────────────────────────────────────────────────

async def run_batch_cycle(
    *,
    conn: sqlite3.Connection,
    config: CSIConfig,
    emitter: UAEmitter | None,
) -> dict[str, Any]:
    """
    Collect un-emitted events, produce a Markdown brief, emit as a single event.

    Returns a status dict for logging / health tracking.
    """
    rows = fetch_undelivered_events(conn)
    if len(rows) < config.batch_min_events:
        return {"status": "skipped", "reason": "below_threshold", "event_count": len(rows)}

    event_ids = [r["event_id"] for r in rows]

    # ── Generate Markdown brief ──
    gemini_key = config.gemini_api_key
    prompt = _build_prompt(rows)
    brief_md: str
    llm_used = False

    if gemini_key:
        try:
            brief_md = await _call_gemini(gemini_key, prompt, model=config.gemini_model)
            llm_used = True
        except Exception as exc:
            logger.warning("Gemini batch brief failed, using fallback: %s", exc)
            brief_md = _fallback_brief(rows)
    else:
        logger.info("No Gemini API key configured; using plain-text batch brief")
        brief_md = _fallback_brief(rows)

    # Extract headline from brief — find first heading with real content
    headline = "CSI Batch Brief"
    for ln in brief_md.split("\n"):
        stripped = ln.lstrip("#").strip().strip("*").strip()
        if not stripped:
            continue
        # Skip lines that are just generic labels
        if stripped.lower() in ("headline", "summary", "overview", "report", "brief"):
            continue
        headline = stripped[:200]
        break
    summary = brief_md[:500]

    # ── Build batch brief event ──
    now_iso = _utc_now()
    batch_event = CreatorSignalEvent(
        event_id=f"batch_{uuid.uuid4().hex[:16]}",
        dedupe_key=f"batch_brief_{now_iso}",
        source="csi_ingester_batch",
        event_type="csi_batch_brief",
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "title": headline,
            "summary": summary,
            "full_report_md": brief_md,
            "event_count": len(rows),
            "source_ids": event_ids[:100],  # cap to avoid huge payloads
            "llm_used": llm_used,
        },
        routing={"priority": "low", "category": "batch_brief"},
        metadata={"batch_interval_seconds": config.batch_interval_seconds},
    )

    # ── Emit to UA ──
    delivered = False
    status_code = 0
    if emitter:
        try:
            delivered, status_code, payload = await emitter.emit_with_retries(
                [batch_event], max_attempts=3,
            )
        except Exception as exc:
            logger.warning("Batch brief emission failed: %s", exc)
    else:
        logger.info("No UAEmitter configured; batch brief generated but not emitted")

    # ── Mark source events as delivered ──
    if delivered or emitter is None:
        # Mark delivered even when emitter is None (events are "consumed" by the brief)
        marked = mark_events_delivered(conn, event_ids)
    else:
        marked = 0

    return {
        "status": "emitted" if delivered else ("generated" if emitter is None else "emit_failed"),
        "event_count": len(rows),
        "brief_headline": headline,
        "llm_used": llm_used,
        "delivered": delivered,
        "status_code": status_code,
        "events_marked": marked,
    }


# ── Helpers ──────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
