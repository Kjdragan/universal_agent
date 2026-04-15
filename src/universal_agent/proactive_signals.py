from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from universal_agent import task_hub


CARD_STATUS_PENDING = "pending"
CARD_STATUS_APPROVED = "approved"
CARD_STATUS_REJECTED = "rejected"
CARD_STATUS_TRACKING = "tracking"
CARD_STATUS_ACTIONED = "actioned"
CARD_STATUS_DELETED = "deleted"
VALID_CARD_STATUSES = {
    CARD_STATUS_PENDING,
    CARD_STATUS_APPROVED,
    CARD_STATUS_REJECTED,
    CARD_STATUS_TRACKING,
    CARD_STATUS_ACTIONED,
    CARD_STATUS_DELETED,
}

YOUTUBE_INTEREST_TERMS = {
    "agent",
    "agents",
    "agentic",
    "claude",
    "claude code",
    "codex",
    "mcp",
    "model",
    "models",
    "llm",
    "eval",
    "evals",
    "harness",
    "workflow",
    "automation",
    "rag",
    "benchmark",
    "tool",
    "framework",
    "coding",
    "developer",
    "prompt",
}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_signal_cards (
            card_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            card_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 2,
            confidence_score REAL NOT NULL DEFAULT 0.0,
            novelty_score REAL NOT NULL DEFAULT 0.0,
            evidence_json TEXT NOT NULL DEFAULT '[]',
            actions_json TEXT NOT NULL DEFAULT '[]',
            feedback_json TEXT NOT NULL DEFAULT '{}',
            selected_action_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_signal_cards_status ON proactive_signal_cards(status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_signal_cards_source ON proactive_signal_cards(source, updated_at DESC)"
    )
    conn.commit()


def list_cards(
    conn: sqlite3.Connection,
    *,
    source: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 80,
) -> list[dict[str, Any]]:
    ensure_schema(conn)
    clauses: list[str] = []
    params: list[Any] = []
    if source and source.strip().lower() != "all":
        clauses.append("source = ?")
        params.append(source.strip().lower())
    if status and status.strip().lower() != "all":
        clauses.append("status = ?")
        params.append(status.strip().lower())
    else:
        clauses.append("status != ?")
        params.append(CARD_STATUS_DELETED)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM proactive_signal_cards
        {where}
        ORDER BY
            CASE status WHEN 'pending' THEN 0 WHEN 'tracking' THEN 1 WHEN 'actioned' THEN 2 ELSE 3 END,
            priority DESC,
            updated_at DESC
        LIMIT ?
        """,
        (*params, max(1, min(int(limit), 500))),
    ).fetchall()
    return [_hydrate_card(dict(row)) for row in rows]


def upsert_generated_card(conn: sqlite3.Connection, card: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    now_iso = _now_iso()
    card_id = str(card.get("card_id") or "").strip()
    if not card_id:
        raise ValueError("card_id is required")
    payload = {
        "card_id": card_id,
        "source": str(card.get("source") or "unknown").strip().lower(),
        "card_type": str(card.get("card_type") or "signal").strip().lower(),
        "title": str(card.get("title") or "Signal candidate").strip()[:300],
        "summary": str(card.get("summary") or "").strip()[:4000],
        "priority": int(max(1, min(4, int(card.get("priority") or 2)))),
        "confidence_score": float(max(0.0, min(1.0, float(card.get("confidence_score") or 0.0)))),
        "novelty_score": float(max(0.0, min(1.0, float(card.get("novelty_score") or 0.0)))),
        "evidence_json": _json_dumps(card.get("evidence") if isinstance(card.get("evidence"), list) else []),
        "actions_json": _json_dumps(card.get("actions") if isinstance(card.get("actions"), list) else []),
        "metadata_json": _json_dumps(card.get("metadata") if isinstance(card.get("metadata"), dict) else {}),
        "created_at": str(card.get("created_at") or now_iso),
        "updated_at": now_iso,
    }
    conn.execute(
        """
        INSERT INTO proactive_signal_cards (
            card_id, source, card_type, title, summary, status, priority,
            confidence_score, novelty_score, evidence_json, actions_json,
            metadata_json, created_at, updated_at
        ) VALUES (
            :card_id, :source, :card_type, :title, :summary, 'pending', :priority,
            :confidence_score, :novelty_score, :evidence_json, :actions_json,
            :metadata_json, :created_at, :updated_at
        )
        ON CONFLICT(card_id) DO UPDATE SET
            source=excluded.source,
            card_type=excluded.card_type,
            title=excluded.title,
            summary=excluded.summary,
            priority=excluded.priority,
            confidence_score=excluded.confidence_score,
            novelty_score=excluded.novelty_score,
            evidence_json=excluded.evidence_json,
            actions_json=excluded.actions_json,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        payload,
    )
    conn.commit()
    return get_card(conn, card_id) or payload


def get_card(conn: sqlite3.Connection, card_id: str) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM proactive_signal_cards WHERE card_id = ? LIMIT 1",
        (card_id,),
    ).fetchone()
    return _hydrate_card(dict(row)) if row else None


def delete_card(conn: sqlite3.Connection, card_id: str) -> bool:
    """Silently delete a card (soft delete) without recording feedback.

    Used to declutter the signal queue — this is NOT a reject and should
    not be treated as a preference signal.
    """
    ensure_schema(conn)
    cursor = conn.execute(
        "UPDATE proactive_signal_cards SET status = ?, updated_at = ? WHERE card_id = ?",
        (CARD_STATUS_DELETED, _now_iso(), card_id,),
    )
    conn.commit()
    return cursor.rowcount > 0


def record_feedback(
    conn: sqlite3.Connection,
    *,
    card_id: str,
    tags: Optional[list[str]] = None,
    text: str = "",
    status: Optional[str] = None,
    actor: str = "dashboard_operator",
) -> dict[str, Any]:
    ensure_schema(conn)
    card = get_card(conn, card_id)
    if card is None:
        raise KeyError(card_id)
    normalized_status = str(status or "").strip().lower()
    if normalized_status and normalized_status not in VALID_CARD_STATUSES:
        raise ValueError(f"unsupported status: {normalized_status}")
    feedback = dict(card.get("feedback") or {})
    history = feedback.get("history") if isinstance(feedback.get("history"), list) else []
    cleaned_tags = [tag.strip().lower() for tag in (tags or []) if str(tag).strip()]
    entry = {
        "actor": actor,
        "tags": sorted(set(cleaned_tags)),
        "text": str(text or "").strip(),
        "created_at": _now_iso(),
    }
    if entry["tags"] or entry["text"]:
        history.append(entry)
    tag_counts = dict(feedback.get("tag_counts") or {})
    for tag in entry["tags"]:
        tag_counts[tag] = int(tag_counts.get(tag) or 0) + 1
    feedback.update({"history": history[-50:], "tag_counts": tag_counts})
    conn.execute(
        """
        UPDATE proactive_signal_cards
        SET feedback_json = ?,
            status = COALESCE(?, status),
            updated_at = ?
        WHERE card_id = ?
        """,
        (
            _json_dumps(feedback),
            normalized_status or None,
            _now_iso(),
            card_id,
        ),
    )
    conn.commit()
    return get_card(conn, card_id) or card


def apply_card_action(
    conn: sqlite3.Connection,
    *,
    card_id: str,
    action_id: str,
    actor: str = "dashboard_operator",
    feedback_tags: Optional[list[str]] = None,
    feedback_text: str = "",
) -> dict[str, Any]:
    ensure_schema(conn)
    card = get_card(conn, card_id)
    if card is None:
        raise KeyError(card_id)
    actions = card.get("actions") if isinstance(card.get("actions"), list) else []
    selected = next(
        (action for action in actions if str(action.get("id") or "") == action_id),
        None,
    )
    if selected is None:
        raise ValueError(f"unsupported action: {action_id}")
    should_create_task = str(selected.get("id") or "") != "track_topic"
    task_id = (
        _create_followup_task(conn, card=card, action=selected, actor=actor)
        if should_create_task
        else ""
    )
    selected_payload = {
        "action": selected,
        "task_id": task_id,
        "actor": actor,
        "created_at": _now_iso(),
    }
    conn.execute(
        """
        UPDATE proactive_signal_cards
        SET status = ?,
            selected_action_json = ?,
            updated_at = ?
        WHERE card_id = ?
        """,
        (
            CARD_STATUS_ACTIONED if should_create_task else CARD_STATUS_TRACKING,
            _json_dumps(selected_payload),
            _now_iso(),
            card_id,
        ),
    )
    conn.commit()
    if feedback_tags or feedback_text:
        record_feedback(
            conn,
            card_id=card_id,
            tags=feedback_tags,
            text=feedback_text,
            actor=actor,
        )
    return get_card(conn, card_id) or card


def sync_generated_cards(
    conn: sqlite3.Connection,
    *,
    csi_db_path: Optional[Path] = None,
    discord_db_path: Optional[Path] = None,
) -> dict[str, int]:
    counts = {"youtube": 0, "discord": 0}
    for card in generate_youtube_cards(csi_db_path):
        upsert_generated_card(conn, card)
        counts["youtube"] += 1
    for card in generate_discord_cards(discord_db_path):
        upsert_generated_card(conn, card)
        counts["discord"] += 1
    return counts


def generate_youtube_cards(csi_db_path: Optional[Path], *, limit: int = 400) -> list[dict[str, Any]]:
    if csi_db_path is None or not csi_db_path.exists():
        return []
    db = sqlite3.connect(str(csi_db_path))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            SELECT
                e.event_id, e.occurred_at, e.subject_json,
                a.transcript_status, a.transcript_chars, a.category,
                a.summary_text, a.analysis_json, a.analyzed_at
            FROM events e
            LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
            WHERE e.source = 'youtube_channel_rss'
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        subject = _json_loads_obj(row["subject_json"])
        if _is_short_subject(subject):
            continue
        analysis = _json_loads_obj(row["analysis_json"])
        items.append(
            {
                "event_id": str(row["event_id"] or ""),
                "occurred_at": str(row["occurred_at"] or ""),
                "video_id": str(subject.get("video_id") or "").strip(),
                "title": str(subject.get("title") or subject.get("media_title") or "").strip(),
                "description": str(subject.get("description") or "").strip(),
                "channel_name": str(subject.get("channel_name") or subject.get("author_name") or "").strip(),
                "channel_id": str(subject.get("channel_id") or "").strip(),
                "url": str(subject.get("url") or "").strip(),
                "thumbnail_url": str(subject.get("thumbnail_url") or "").strip(),
                "transcript_status": str(row["transcript_status"] or "missing").strip().lower(),
                "transcript_chars": int(row["transcript_chars"] or 0),
                "category": str(row["category"] or analysis.get("category") or "").strip(),
                "summary_text": str(row["summary_text"] or "").strip(),
                "analysis": analysis,
                "analyzed_at": str(row["analyzed_at"] or "").strip(),
            }
        )

    cards: list[dict[str, Any]] = []
    cards.extend(_youtube_cluster_cards(items))
    cards.extend(_youtube_diamond_cards(items))
    return cards[:50]


def generate_discord_cards(discord_db_path: Optional[Path], *, limit: int = 60) -> list[dict[str, Any]]:
    if discord_db_path is None or not discord_db_path.exists():
        return []
    db = sqlite3.connect(str(discord_db_path))
    db.row_factory = sqlite3.Row
    cards: list[dict[str, Any]] = []
    try:
        insight_rows = db.execute(
            """
            SELECT i.id, i.topic, i.summary, i.sentiment, i.urgency, i.confidence,
                   i.created_at, tb.channel_id, c.name AS channel_name, srv.name AS server_name
            FROM insights i
            JOIN triage_batches tb ON i.batch_id = tb.id
            LEFT JOIN channels c ON tb.channel_id = c.id
            LEFT JOIN servers srv ON c.server_id = srv.id
            ORDER BY i.created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    except sqlite3.Error:
        insight_rows = []
    finally:
        db.close()
    for row in insight_rows:
        urgency = str(row["urgency"] or "low").lower()
        confidence = float(row["confidence"] or 0.0)
        if urgency == "low" and confidence < 0.65:
            continue
        topic = str(row["topic"] or "Discord insight").strip()
        source_label = " / ".join(
            part for part in [str(row["server_name"] or "").strip(), str(row["channel_name"] or "").strip()] if part
        )
        cards.append(
            {
                "card_id": _card_id("discord", str(row["id"])),
                "source": "discord",
                "card_type": "insight",
                "title": f"Discord insight: {topic[:180]}",
                "summary": str(row["summary"] or "").strip()[:1200],
                "priority": 3 if urgency == "high" else 2,
                "confidence_score": max(0.35, min(0.95, confidence)),
                "novelty_score": 0.55,
                "evidence": [
                    {
                        "source": "discord",
                        "label": source_label or "Discord",
                        "created_at": str(row["created_at"] or ""),
                        "summary": str(row["summary"] or "").strip()[:500],
                    }
                ],
                "actions": _discord_actions(topic),
                "metadata": {
                    "discord_insight_id": int(row["id"] or 0),
                    "urgency": urgency,
                    "sentiment": str(row["sentiment"] or ""),
                    "topic": topic,
                },
                "created_at": str(row["created_at"] or _now_iso()),
            }
        )
    return cards


def _youtube_cluster_cards(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        for topic in _extract_topics(" ".join([item.get("title", ""), item.get("description", "")])):
            buckets.setdefault(topic, []).append(item)
    cards: list[dict[str, Any]] = []
    for topic, rows in buckets.items():
        channels = {row.get("channel_id") or row.get("channel_name") for row in rows if row.get("channel_id") or row.get("channel_name")}
        transcripted = [row for row in rows if row.get("transcript_status") == "ok"]
        if len(rows) < 3 and len(channels) < 2:
            continue
        confidence = 0.48 + min(0.22, len(rows) * 0.03) + min(0.18, len(channels) * 0.04)
        if transcripted:
            confidence += 0.14
        cards.append(
            {
                "card_id": _card_id("youtube-cluster", topic),
                "source": "youtube",
                "card_type": "cluster",
                "title": f"YouTube topic cluster: {topic}",
                "summary": (
                    f"{len(rows)} non-Short YouTube upload(s) across {len(channels)} channel(s) mention {topic}. "
                    f"{len(transcripted)} already have transcript-backed analysis."
                ),
                "priority": 3 if transcripted or len(channels) >= 3 else 2,
                "confidence_score": min(0.92, confidence),
                "novelty_score": 0.7 if len(channels) >= 3 else 0.55,
                "evidence": [_youtube_evidence(row) for row in rows[:5]],
                "actions": _youtube_actions(topic=topic, has_transcripts=bool(transcripted), cluster=True),
                "metadata": {
                    "topic": topic,
                    "video_count": len(rows),
                    "distinct_channels": len(channels),
                    "transcript_backed_count": len(transcripted),
                },
            }
        )
    return cards


def _youtube_diamond_cards(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        score = _youtube_item_score(item)
        if score >= 0.58:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    cards: list[dict[str, Any]] = []
    for score, item in scored[:20]:
        title = item.get("title") or "YouTube candidate"
        has_transcript = item.get("transcript_status") == "ok"
        summary = item.get("summary_text") if has_transcript and item.get("summary_text") else item.get("description")
        if not summary:
            summary = f"{title} looks aligned with current agent/build learning interests."
        cards.append(
            {
                "card_id": _card_id("youtube-video", item.get("event_id") or item.get("url") or title),
                "source": "youtube",
                "card_type": "diamond" if not has_transcript else "transcript_insight",
                "title": f"YouTube candidate: {title[:190]}",
                "summary": str(summary).strip()[:1400],
                "priority": 3 if has_transcript else 2,
                "confidence_score": min(0.94, score),
                "novelty_score": 0.62 if not has_transcript else 0.68,
                "evidence": [_youtube_evidence(item)],
                "actions": _youtube_actions(topic=title, has_transcripts=has_transcript, cluster=False),
                "metadata": {
                    "video_id": item.get("video_id"),
                    "event_id": item.get("event_id"),
                    "youtube_url": item.get("url"),
                    "transcript_status": item.get("transcript_status"),
                    "category": item.get("category"),
                },
                "created_at": item.get("occurred_at") or _now_iso(),
            }
        )
    return cards


def _youtube_item_score(item: dict[str, Any]) -> float:
    text = " ".join([item.get("title", ""), item.get("description", ""), item.get("summary_text", "")]).lower()
    hits = sum(1 for term in YOUTUBE_INTEREST_TERMS if term in text)
    score = 0.36 + min(0.28, hits * 0.06)
    if item.get("transcript_status") == "ok":
        score += 0.22
    if item.get("description"):
        score += 0.05
    if item.get("channel_name"):
        score += 0.03
    if any(term in text for term in ("tutorial", "guide", "walkthrough", "deep dive", "explained", "build")):
        score += 0.12
    if any(term in text for term in ("reaction", "drama", "shocking", "you won't believe")):
        score -= 0.08
    return max(0.0, min(1.0, score))


def _extract_topics(text: str) -> list[str]:
    lowered = re.sub(r"\s+", " ", str(text or "").lower())
    topics: list[str] = []
    for phrase in (
        "claude code",
        "openai codex",
        "agentic coding",
        "mcp server",
        "model context protocol",
        "multi-agent",
        "workflow automation",
        "ai agent",
        "llm eval",
        "rag pipeline",
    ):
        if phrase in lowered:
            topics.append(phrase)
    tokens = re.findall(r"[a-z][a-z0-9_-]{2,}", lowered)
    for token in tokens:
        if token in YOUTUBE_INTEREST_TERMS:
            topics.append(token)
    return sorted(set(topics))[:8]


def _youtube_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "youtube",
        "title": item.get("title"),
        "channel": item.get("channel_name"),
        "url": item.get("url"),
        "thumbnail_url": item.get("thumbnail_url"),
        "occurred_at": item.get("occurred_at"),
        "transcript_status": item.get("transcript_status"),
        "summary": (item.get("summary_text") or item.get("description") or "")[:600],
    }


def _youtube_actions(*, topic: str, has_transcripts: bool, cluster: bool) -> list[dict[str, str]]:
    if has_transcripts:
        return [
            {"id": "research_further", "label": "Research Further", "description": "Create a follow-up research task using the transcript-backed evidence."},
            {"id": "create_wiki", "label": "Create Wiki", "description": "Create a task to build a NotebookLM-backed knowledge base. Delegate to the `notebooklm-operator` sub-agent to: (1) create a NotebookLM notebook, (2) run NLM research, (3) generate artifacts via NLM studio (report, infographic) using parallel batch creation, (4) download artifacts, (5) register KB via `kb_register`, (6) ingest report via `wiki_ingest_external_source`. Do NOT use `generate_image` or generic web scraping — NLM handles research and artifact generation end-to-end."},
            {"id": "build_demo", "label": "Build Demo", "description": "Create a task to prototype or demonstrate the technique if applicable."},
        ]
    return [
        {"id": "fetch_transcripts", "label": "Fetch More Transcripts", "description": "Create a capped transcript sampling task for representative non-Short videos."},
        {"id": "track_topic", "label": "Track Topic", "description": "Keep watching this topic before spending transcript budget."},
        {"id": "research_further", "label": "Research Further", "description": "Create a lightweight research task from metadata and available evidence."},
        {"id": "create_wiki", "label": "Create Wiki", "description": "Create a task to build a NotebookLM-backed knowledge base. Delegate to the `notebooklm-operator` sub-agent to: (1) create a NotebookLM notebook, (2) run NLM research, (3) generate artifacts via NLM studio (report, infographic) using parallel batch creation, (4) download artifacts, (5) register KB via `kb_register`, (6) ingest report via `wiki_ingest_external_source`. Do NOT use `generate_image` or generic web scraping — NLM handles research and artifact generation end-to-end."},
    ] if cluster else [
        {"id": "fetch_transcripts", "label": "Fetch Transcript", "description": "Create a task to fetch and analyze this non-Short video transcript."},
        {"id": "track_topic", "label": "Track Topic", "description": "Keep watching for related videos before deeper work."},
        {"id": "create_wiki", "label": "Create Wiki", "description": "Create a task to build a NotebookLM-backed knowledge base. Delegate to the `notebooklm-operator` sub-agent to: (1) create a NotebookLM notebook, (2) run NLM research, (3) generate artifacts via NLM studio (report, infographic) using parallel batch creation, (4) download artifacts, (5) register KB via `kb_register`, (6) ingest report via `wiki_ingest_external_source`. Do NOT use `generate_image` or generic web scraping — NLM handles research and artifact generation end-to-end."},
    ]


def _discord_actions(topic: str) -> list[dict[str, str]]:
    return [
        {"id": "research_further", "label": "Research Further", "description": "Create a research task from this Discord intelligence signal."},
        {"id": "create_wiki", "label": "Create Wiki", "description": "Create a task to build a NotebookLM-backed knowledge base. Delegate to the `notebooklm-operator` sub-agent to: (1) create a NotebookLM notebook, (2) run NLM research, (3) generate artifacts via NLM studio (report, infographic) using parallel batch creation, (4) download artifacts, (5) register KB via `kb_register`, (6) ingest report via `wiki_ingest_external_source`. Do NOT use `generate_image` or generic web scraping — NLM handles research and artifact generation end-to-end."},
        {"id": "track_topic", "label": "Track Topic", "description": "Keep watching this topic before deeper work."},
    ]


def _create_followup_task(
    conn: sqlite3.Connection,
    *,
    card: dict[str, Any],
    action: dict[str, Any],
    actor: str,
) -> str:
    task_id = f"proactive_signal:{_short_hash(card['card_id'] + ':' + str(action.get('id') or 'action'))}"
    action_label = str(action.get("label") or action.get("id") or "Follow up")
    evidence = card.get("evidence") if isinstance(card.get("evidence"), list) else []
    description = "\n".join(
        [
            f"Proactive signal action: {action_label}",
            "",
            f"Signal: {card.get('title')}",
            "",
            str(card.get("summary") or ""),
            "",
            f"Action instructions: {action.get('description') or action_label}",
            "",
            "Evidence:",
            json.dumps(evidence[:8], indent=2, ensure_ascii=True),
        ]
    )
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "proactive_signal",
            "source_ref": str(card.get("card_id") or ""),
            "title": f"{action_label}: {card.get('title')}",
            "description": description,
            "project_key": "proactive",
            "priority": max(1, min(4, int(card.get("priority") or 2))),
            "labels": ["proactive-signal", str(card.get("source") or "signal"), str(action.get("id") or "action")],
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": str(action.get("id") or "") != "track_topic",
            "metadata": {
                "source": "proactive_signal",
                "card_id": card.get("card_id"),
                "action": action,
                "actor": actor,
                "evidence": evidence[:12],
            },
        },
    )
    return task_id


def _hydrate_card(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = _json_loads_list(row.pop("evidence_json", "[]"))
    row["actions"] = _json_loads_list(row.pop("actions_json", "[]"))
    row["feedback"] = _json_loads_obj(row.pop("feedback_json", "{}"))
    row["selected_action"] = _json_loads_obj(row.pop("selected_action_json", "{}"))
    row["metadata"] = _json_loads_obj(row.pop("metadata_json", "{}"))
    return row


def _json_loads_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(str(raw or "[]"))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _card_id(prefix: str, seed: str) -> str:
    return f"{prefix}:{_short_hash(seed)}"


def _short_hash(seed: str) -> str:
    return hashlib.sha1(str(seed or "").encode("utf-8")).hexdigest()[:16]


def _is_short_subject(subject: dict[str, Any]) -> bool:
    url = str(subject.get("url") or "").lower()
    if "/shorts/" in url:
        return True
    try:
        duration = int(subject.get("duration") or 0)
    except Exception:
        duration = 0
    return duration > 0 and duration <= 75


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def distill_feedback_to_rules(card_dict: dict[str, Any], feedback_text: str, feedback_tags: list[str] = None) -> None:
    """Asynchronously evaluate raw feedback and update proactive generation rules."""
    import litellm
    import logging
    from universal_agent.utils.model_resolution import resolve_sonnet

    feedback_tags = feedback_tags or []
    logger = logging.getLogger(__name__)
    if not feedback_text.strip() and not feedback_tags:
        return

    # Find the repository doc path
    repo_root = Path(__file__).parent.parent.parent.parent
    docs_dir = repo_root / "docs" / "proactive_signals"
    rules_path = docs_dir / "generation_rules.md"
    
    current_rules = ""
    if rules_path.exists():
        current_rules = rules_path.read_text(encoding="utf-8")
        
    prompt = f"""You are the Universal Agent Rule Distiller.
The user just provided feedback on a Proactive Signal card (either by clicking an icon tag or writing text).
Your job is to read the current rules, interpret the feedback against the card context, and rewrite the rules document to cleanly incorporate the user's preference constraints without destroying the underlying rules!
If they give a general instruction, place it under General Guidelines. If it involves a specific platform or topic keyword, create or update the appropriate section (e.g. Source Constraints -> YouTube, or Topic Constraints -> [Topic]).

Current Generation Rules:
-------------------------------------
{current_rules}
-------------------------------------

Original Card Details:
Source: {card_dict.get('source')}
Title: {card_dict.get('title')}
Summary: {card_dict.get('summary')}

User's Icon Feedback Tags: {', '.join(feedback_tags) if feedback_tags else 'None'}
User's Raw Feedback Text:
-------------------------------------
{feedback_text}
-------------------------------------

Respond with ONLY the markdown content for the updated rules file. Do not include introductory or concluding conversation. Maintain the existing markdown headers.
"""
    model = resolve_sonnet()
    try:
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2500,
        )
        new_rules = response.choices[0].message.content.strip()
        if new_rules.startswith("```md"):
            new_rules = new_rules[5:]
        if new_rules.startswith("```markdown"):
            new_rules = new_rules[11:]
        if new_rules.startswith("```"):
            new_rules = new_rules[3:]
        if new_rules.endswith("```"):
            new_rules = new_rules[:-3]
        
        docs_dir.mkdir(parents=True, exist_ok=True)
        rules_path.write_text(new_rules.strip(), encoding="utf-8")
        logger.info("Successfully distilled feedback into generation_rules.md")
    except Exception as exc:
        logger.error(f"Failed to distill feedback: {exc}", exc_info=True)
