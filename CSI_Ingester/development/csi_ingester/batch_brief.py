"""Batched brief scheduler — collects events, summarises via LLM, emits one digest."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import sqlite3
from typing import Any
import uuid

import httpx

from csi_ingester.config import CSIConfig
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.store import events as event_store

logger = logging.getLogger(__name__)

# ── Z.AI summarisation (haiku-tier glm-4.5-air via Anthropic-compatible API) ─

_ZAI_BASE = "https://api.z.ai/api/anthropic"
_DEFAULT_MODEL = "glm-4.5-air"

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


async def _call_zai(api_key: str, prompt: str, *, model: str = _DEFAULT_MODEL, timeout: int = 60) -> str:
    """Call Z.AI's Anthropic-compatible chat API and return the generated text.

    Default model is ``glm-4.5-air`` (haiku-tier). The lane is known to be
    occasionally flaky (~6 min hangs documented in
    ``src/universal_agent/utils/model_resolution.py``), so the timeout is
    bounded and the caller falls back to ``_fallback_brief`` on any failure.
    """
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 2048,
        "temperature": 0.3,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    url = f"{_ZAI_BASE}/v1/messages"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
    content_blocks = body.get("content") or []
    if not content_blocks:
        raise ValueError("Z.AI returned no content blocks")
    text_parts = [
        str(block.get("text") or "")
        for block in content_blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "".join(text_parts).strip()
    if not text:
        raise ValueError("Z.AI returned empty text")
    return text


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
    zai_key = config.zai_api_key
    prompt = _build_prompt(rows)
    brief_md: str
    llm_used = False

    if zai_key:
        try:
            brief_md = await _call_zai(zai_key, prompt, model=config.zai_model)
            llm_used = True
        except Exception as exc:
            logger.warning("Z.AI batch brief failed, using fallback: %s", exc)
            brief_md = _fallback_brief(rows)
    else:
        logger.info("No Z.AI API key configured; using plain-text batch brief")
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
