"""Cross-channel convergence detection for proactive intelligence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from universal_agent import task_hub
from universal_agent.services.proactive_artifacts import ARTIFACT_STATUS_CANDIDATE, make_artifact_id, upsert_artifact

SignatureMatcher = Callable[[dict[str, Any], list[dict[str, Any]]], list[dict[str, Any]]]

_SIGNATURE_SYSTEM = """\
You extract compact topic signatures from AI/developer video transcripts for a proactive intelligence system.
Return ONLY JSON with this shape:
{
  "primary_topics": ["1-3 short topic names"],
  "secondary_topics": ["0-5 related topics"],
  "key_claims": ["2-6 concise claims from the source"],
  "content_type": "tutorial" | "analysis" | "news" | "opinion" | "other"
}
"""

_MATCH_SYSTEM = """\
You judge whether recent videos from independent channels substantially cover the same subject.
Return ONLY JSON:
{
  "matches": [
    {"video_id": "id", "reason": "short reason"}
  ]
}
Match on semantic topic convergence, not exact wording. Exclude weakly related items.
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proactive_topic_signatures (
            video_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL DEFAULT '',
            channel_name TEXT NOT NULL DEFAULT '',
            video_title TEXT NOT NULL DEFAULT '',
            video_url TEXT NOT NULL DEFAULT '',
            ingested_at TEXT NOT NULL,
            primary_topics_json TEXT NOT NULL DEFAULT '[]',
            secondary_topics_json TEXT NOT NULL DEFAULT '[]',
            key_claims_json TEXT NOT NULL DEFAULT '[]',
            content_type TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_topic_signatures_ingested
            ON proactive_topic_signatures(ingested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_proactive_topic_signatures_channel
            ON proactive_topic_signatures(channel_id, ingested_at DESC);

        CREATE TABLE IF NOT EXISTS proactive_convergence_events (
            event_id TEXT PRIMARY KEY,
            primary_topic TEXT NOT NULL,
            video_ids_json TEXT NOT NULL DEFAULT '[]',
            channel_names_json TEXT NOT NULL DEFAULT '[]',
            brief_task_id TEXT NOT NULL DEFAULT '',
            artifact_id TEXT NOT NULL DEFAULT '',
            feedback_score INTEGER,
            detected_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_convergence_events_detected
            ON proactive_convergence_events(detected_at DESC);
        """
    )
    conn.commit()


def upsert_topic_signature(
    conn: sqlite3.Connection,
    *,
    video_id: str,
    channel_id: str = "",
    channel_name: str = "",
    video_title: str = "",
    video_url: str = "",
    ingested_at: str = "",
    primary_topics: list[str] | None = None,
    secondary_topics: list[str] | None = None,
    key_claims: list[str] | None = None,
    content_type: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    clean_video_id = str(video_id or "").strip()
    if not clean_video_id:
        raise ValueError("video_id is required")
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO proactive_topic_signatures (
            video_id, channel_id, channel_name, video_title, video_url, ingested_at,
            primary_topics_json, secondary_topics_json, key_claims_json,
            content_type, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            channel_id=excluded.channel_id,
            channel_name=excluded.channel_name,
            video_title=excluded.video_title,
            video_url=excluded.video_url,
            ingested_at=excluded.ingested_at,
            primary_topics_json=excluded.primary_topics_json,
            secondary_topics_json=excluded.secondary_topics_json,
            key_claims_json=excluded.key_claims_json,
            content_type=excluded.content_type,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            clean_video_id,
            str(channel_id or "").strip(),
            str(channel_name or "").strip(),
            str(video_title or "").strip(),
            str(video_url or "").strip(),
            ingested_at or now,
            _json_dumps(_clean_list(primary_topics or [])),
            _json_dumps(_clean_list(secondary_topics or [])),
            _json_dumps(_clean_list(key_claims or [])),
            str(content_type or "").strip(),
            _json_dumps(metadata or {}),
            now,
            now,
        ),
    )
    conn.commit()
    return get_topic_signature(conn, clean_video_id) or {}


def sync_topic_signatures_from_csi(
    conn: sqlite3.Connection,
    *,
    csi_db_path: Path | None,
    limit: int = 400,
) -> dict[str, int]:
    """Sync transcript-backed CSI RSS analysis rows into topic signatures."""
    if csi_db_path is None or not csi_db_path.exists():
        return {"seen": 0, "upserted": 0, "convergence_events": 0}
    ensure_schema(conn)
    db = sqlite3.connect(str(csi_db_path))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            SELECT
                e.event_id, e.occurred_at, e.subject_json,
                a.category, a.summary_text, a.analysis_json, a.analyzed_at,
                a.transcript_status
            FROM events e
            LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
            WHERE e.source = 'youtube_channel_rss'
              AND a.summary_text IS NOT NULL
              AND a.summary_text != ''
            ORDER BY COALESCE(a.analyzed_at, e.occurred_at) DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    except sqlite3.Error:
        return {"seen": 0, "upserted": 0, "convergence_events": 0}
    finally:
        db.close()

    upserted = 0
    convergence_events = 0
    for row in rows:
        subject = _json_loads_obj(row["subject_json"])
        analysis = _json_loads_obj(row["analysis_json"])
        video_id = str(subject.get("video_id") or row["event_id"] or "").strip()
        if not video_id:
            continue
        topics = _analysis_topics(analysis=analysis, category=str(row["category"] or ""), title=str(subject.get("title") or ""))
        signature = upsert_topic_signature(
            conn,
            video_id=video_id,
            channel_id=str(subject.get("channel_id") or "").strip(),
            channel_name=str(subject.get("channel_name") or subject.get("author_name") or "").strip(),
            video_title=str(subject.get("title") or subject.get("media_title") or "").strip(),
            video_url=str(subject.get("url") or "").strip(),
            ingested_at=str(row["analyzed_at"] or row["occurred_at"] or _now_iso()),
            primary_topics=topics[:3],
            secondary_topics=topics[3:8],
            key_claims=_analysis_claims(analysis=analysis, summary_text=str(row["summary_text"] or "")),
            content_type=str(row["category"] or analysis.get("category") or "other").strip(),
            metadata={
                "event_id": str(row["event_id"] or ""),
                "source": "csi_rss_analysis",
                "transcript_status": str(row["transcript_status"] or ""),
            },
        )
        upserted += 1
        if detect_and_queue_convergence(conn, signature=signature):
            convergence_events += 1
    return {"seen": len(rows), "upserted": upserted, "convergence_events": convergence_events}


def get_topic_signature(conn: sqlite3.Connection, video_id: str) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM proactive_topic_signatures WHERE video_id = ? LIMIT 1",
        (str(video_id or "").strip(),),
    ).fetchone()
    return _hydrate_signature(dict(row)) if row else None


def detect_and_queue_convergence(
    conn: sqlite3.Connection,
    *,
    signature: dict[str, Any],
    window_hours: int = 72,
    min_channels: int = 2,
    matcher: SignatureMatcher | None = None,
) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    candidates = _recent_other_channel_signatures(conn, signature=signature, window_hours=window_hours)
    matched = (matcher or _overlap_matcher)(signature, candidates)
    channels = {
        str(signature.get("channel_name") or signature.get("channel_id") or "").strip(),
        *[str(item.get("channel_name") or item.get("channel_id") or "").strip() for item in matched],
    }
    channels.discard("")
    if len(channels) < max(2, int(min_channels or 2)):
        return None
    participants = [signature, *matched]
    return create_convergence_brief_task(conn, signatures=participants)


async def extract_topic_signature_from_text(
    *,
    video_id: str,
    title: str = "",
    transcript_text: str = "",
    summary_text: str = "",
    channel_id: str = "",
    channel_name: str = "",
    video_url: str = "",
    ingested_at: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract a topic signature using an LLM, with deterministic fallback."""
    user = "\n".join(
        [
            f"Video ID: {video_id}",
            f"Title: {title}",
            f"Channel: {channel_name or channel_id}",
            f"Summary: {summary_text[:4000]}",
            "Transcript excerpt:",
            transcript_text[:12000],
        ]
    )
    try:
        from universal_agent.services.llm_classifier import _call_llm, _parse_json_response

        raw = await _call_llm(system=_SIGNATURE_SYSTEM, user=user, max_tokens=900)
        parsed = _parse_json_response(raw)
    except Exception as exc:
        parsed = _fallback_signature(title=title, summary_text=summary_text, error=str(exc))

    return {
        "video_id": str(video_id or "").strip(),
        "channel_id": str(channel_id or "").strip(),
        "channel_name": str(channel_name or "").strip(),
        "video_title": str(title or "").strip(),
        "video_url": str(video_url or "").strip(),
        "ingested_at": ingested_at or _now_iso(),
        "primary_topics": _clean_list(parsed.get("primary_topics") if isinstance(parsed, dict) else []),
        "secondary_topics": _clean_list(parsed.get("secondary_topics") if isinstance(parsed, dict) else []),
        "key_claims": _clean_list(parsed.get("key_claims") if isinstance(parsed, dict) else []),
        "content_type": str((parsed or {}).get("content_type") or "other").strip().lower(),
        "metadata": {**(metadata or {}), "signature_method": "llm" if "fallback_error" not in (parsed or {}) else "fallback", **({"fallback_error": parsed.get("fallback_error")} if isinstance(parsed, dict) and parsed.get("fallback_error") else {})},
    }


async def llm_match_signatures(
    signature: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Use an LLM to match semantically convergent topic signatures."""
    if not candidates:
        return []
    compact_candidates = [
        {
            "video_id": item.get("video_id"),
            "channel": item.get("channel_name") or item.get("channel_id"),
            "title": item.get("video_title"),
            "primary_topics": item.get("primary_topics"),
            "secondary_topics": item.get("secondary_topics"),
            "key_claims": item.get("key_claims"),
        }
        for item in candidates[:40]
    ]
    user = json.dumps(
        {
            "new_signature": {
                "video_id": signature.get("video_id"),
                "channel": signature.get("channel_name") or signature.get("channel_id"),
                "title": signature.get("video_title"),
                "primary_topics": signature.get("primary_topics"),
                "secondary_topics": signature.get("secondary_topics"),
                "key_claims": signature.get("key_claims"),
            },
            "recent_candidates": compact_candidates,
        },
        ensure_ascii=True,
    )
    try:
        from universal_agent.services.llm_classifier import _call_llm, _parse_json_response

        raw = await _call_llm(system=_MATCH_SYSTEM, user=user, max_tokens=1200)
        parsed = _parse_json_response(raw)
        matched_ids = {
            str(item.get("video_id") or "").strip()
            for item in (parsed.get("matches") if isinstance(parsed, dict) else []) or []
            if isinstance(item, dict)
        }
        matched = [item for item in candidates if str(item.get("video_id") or "").strip() in matched_ids]
        if matched:
            return matched
    except Exception:
        pass
    return _overlap_matcher(signature, candidates)


async def detect_and_queue_convergence_llm(
    conn: sqlite3.Connection,
    *,
    signature: dict[str, Any],
    window_hours: int = 72,
    min_channels: int = 2,
) -> Optional[dict[str, Any]]:
    candidates = _recent_other_channel_signatures(conn, signature=signature, window_hours=window_hours)
    matched = await llm_match_signatures(signature, candidates)
    return detect_and_queue_convergence(
        conn,
        signature=signature,
        window_hours=window_hours,
        min_channels=min_channels,
        matcher=lambda _signature, _candidates: matched,
    )


def create_convergence_brief_task(
    conn: sqlite3.Connection,
    *,
    signatures: list[dict[str, Any]],
) -> dict[str, Any]:
    ensure_schema(conn)
    if len(signatures) < 2:
        raise ValueError("at least two signatures are required")
    primary_topic = _primary_topic(signatures)
    video_ids = [str(item.get("video_id") or "").strip() for item in signatures if str(item.get("video_id") or "").strip()]
    event_id = _convergence_event_id(primary_topic=primary_topic, video_ids=video_ids)
    task_id = f"convergence-brief:{event_id.removeprefix('conv_')}"
    preference_context = _preference_context(conn, task_type="convergence_brief", topic_tags=["convergence", primary_topic])
    description = _brief_task_description(
        primary_topic=primary_topic,
        signatures=signatures,
        preference_context=preference_context,
    )
    task = task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "convergence_detection",
            "source_ref": event_id,
            "title": f"ATLAS convergence brief: {primary_topic}",
            "description": description,
            "project_key": "proactive",
            "priority": 3,
            "labels": ["agent-ready", "convergence", "atlas", "research"],
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": True,
            "trigger_type": "heartbeat_poll",
            "metadata": {
                "source": "convergence_detection",
                "event_id": event_id,
                "primary_topic": primary_topic,
                "video_ids": video_ids,
                "preferred_vp": "vp.general.primary",
            },
        },
    )
    artifact = upsert_artifact(
        conn,
        artifact_id=make_artifact_id(
            source_kind="convergence_detection",
            source_ref=event_id,
            artifact_type="convergence_brief_task",
            title=primary_topic,
        ),
        artifact_type="convergence_brief_task",
        source_kind="convergence_detection",
        source_ref=event_id,
        title=str(task.get("title") or ""),
        summary=f"Queued ATLAS convergence brief for {len(signatures)} independent sources on {primary_topic}.",
        status=ARTIFACT_STATUS_CANDIDATE,
        priority=3,
        topic_tags=["convergence", primary_topic],
        metadata={"task_id": task_id, "event_id": event_id, "video_ids": video_ids},
    )
    _record_convergence_event(conn, event_id=event_id, primary_topic=primary_topic, signatures=signatures, task_id=task_id, artifact_id=artifact["artifact_id"])
    return {"event": get_convergence_event(conn, event_id), "task": task, "artifact": artifact}


def get_convergence_event(conn: sqlite3.Connection, event_id: str) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM proactive_convergence_events WHERE event_id = ? LIMIT 1",
        (str(event_id or "").strip(),),
    ).fetchone()
    return _hydrate_event(dict(row)) if row else None


def _recent_other_channel_signatures(
    conn: sqlite3.Connection,
    *,
    signature: dict[str, Any],
    window_hours: int,
) -> list[dict[str, Any]]:
    ingested = _parse_time(signature.get("ingested_at")) or datetime.now(timezone.utc)
    start = (ingested - timedelta(hours=max(1, int(window_hours or 72)))).isoformat()
    end = ingested.isoformat()
    channel_id = str(signature.get("channel_id") or "").strip()
    video_id = str(signature.get("video_id") or "").strip()
    rows = conn.execute(
        """
        SELECT *
        FROM proactive_topic_signatures
        WHERE ingested_at >= ?
          AND ingested_at <= ?
          AND video_id != ?
          AND (? = '' OR channel_id != ?)
        ORDER BY ingested_at DESC
        LIMIT 80
        """,
        (start, end, video_id, channel_id, channel_id),
    ).fetchall()
    return [_hydrate_signature(dict(row)) for row in rows]


def _overlap_matcher(signature: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base = _topic_set(signature)
    if not base:
        return []
    matches = []
    for candidate in candidates:
        overlap = base & _topic_set(candidate)
        if overlap:
            matches.append(candidate)
    return matches


def _topic_set(signature: dict[str, Any]) -> set[str]:
    topics = [*signature.get("primary_topics", []), *signature.get("secondary_topics", [])]
    return {str(topic or "").strip().lower() for topic in topics if str(topic or "").strip()}


def _analysis_topics(*, analysis: dict[str, Any], category: str, title: str) -> list[str]:
    raw_topics: list[Any] = []
    for key in ("themes", "topics", "primary_topics", "tags"):
        value = analysis.get(key)
        if isinstance(value, list):
            raw_topics.extend(value)
    if category:
        raw_topics.append(category)
    if not raw_topics:
        raw_topics.extend(_fallback_signature(title=title, summary_text="").get("primary_topics", []))
    return _clean_list(raw_topics)[:8]


def _analysis_claims(*, analysis: dict[str, Any], summary_text: str) -> list[str]:
    raw_claims: list[Any] = []
    for key in ("key_claims", "claims", "takeaways"):
        value = analysis.get(key)
        if isinstance(value, list):
            raw_claims.extend(value)
    if not raw_claims and summary_text:
        raw_claims.append(summary_text[:300])
    return _clean_list(raw_claims)[:8]


def _fallback_signature(*, title: str, summary_text: str, error: str = "") -> dict[str, Any]:
    words = [
        word.strip(".,:;!?()[]{}\"'").lower()
        for word in f"{title} {summary_text}".split()
    ]
    stop = {"the", "and", "for", "with", "from", "this", "that", "into", "about", "your", "you", "are", "how", "why"}
    topics = []
    for word in words:
        if len(word) < 4 or word in stop:
            continue
        if word not in topics:
            topics.append(word)
        if len(topics) >= 3:
            break
    return {
        "primary_topics": topics or ["emerging topic"],
        "secondary_topics": [],
        "key_claims": [summary_text[:240]] if summary_text else [],
        "content_type": "other",
        "fallback_error": error,
    }


def _primary_topic(signatures: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for signature in signatures:
        for topic in signature.get("primary_topics") or []:
            clean = str(topic or "").strip()
            if clean:
                counts[clean] = counts.get(clean, 0) + 1
    if not counts:
        return "emerging topic"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[0][0]


def _brief_task_description(*, primary_topic: str, signatures: list[dict[str, Any]], preference_context: str = "") -> str:
    lines = [
        f"Generate a convergence brief about: {primary_topic}",
        "",
        "Multiple independent channels covered this topic recently.",
        "",
        "Sources:",
    ]
    for item in signatures:
        claims = "; ".join(item.get("key_claims") or []) or "(no extracted claims)"
        lines.append(
            f"- {item.get('channel_name') or item.get('channel_id')}: {item.get('video_title') or item.get('video_id')} | {item.get('video_url') or ''} | claims: {claims}"
        )
    lines.extend(
        [
            "",
            "Produce a concise brief with:",
            "1. CONVERGENCE SIGNAL: what topic is converging and why now.",
            "2. CONSENSUS: where sources agree.",
            "3. DIVERGENCE: where sources differ.",
            "4. SO WHAT: why Kevin should care and what is actionable.",
            "",
            "Store the final brief as a durable artifact and make it suitable for Simone review email.",
        ]
    )
    if preference_context:
        lines.extend(["", "Preference context:", preference_context])
    return "\n".join(lines)


def _preference_context(conn: sqlite3.Connection, *, task_type: str, topic_tags: list[str]) -> str:
    try:
        from universal_agent.services.proactive_preferences import get_delegation_context

        return get_delegation_context(conn, task_type=task_type, topic_tags=topic_tags)
    except Exception:
        return ""


def _record_convergence_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    primary_topic: str,
    signatures: list[dict[str, Any]],
    task_id: str,
    artifact_id: str,
) -> None:
    now = _now_iso()
    video_ids = [str(item.get("video_id") or "").strip() for item in signatures if str(item.get("video_id") or "").strip()]
    channel_names = [str(item.get("channel_name") or item.get("channel_id") or "").strip() for item in signatures]
    conn.execute(
        """
        INSERT INTO proactive_convergence_events (
            event_id, primary_topic, video_ids_json, channel_names_json,
            brief_task_id, artifact_id, detected_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
            brief_task_id=excluded.brief_task_id,
            artifact_id=excluded.artifact_id,
            metadata_json=excluded.metadata_json
        """,
        (
            event_id,
            primary_topic,
            _json_dumps(video_ids),
            _json_dumps(channel_names),
            task_id,
            artifact_id,
            now,
            _json_dumps({"source_count": len(signatures)}),
        ),
    )
    conn.commit()


def _convergence_event_id(*, primary_topic: str, video_ids: list[str]) -> str:
    seed = "|".join([primary_topic.lower(), *sorted(video_ids)])
    return f"conv_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def _hydrate_signature(row: dict[str, Any]) -> dict[str, Any]:
    row["primary_topics"] = _json_loads_list(row.pop("primary_topics_json", "[]"))
    row["secondary_topics"] = _json_loads_list(row.pop("secondary_topics_json", "[]"))
    row["key_claims"] = _json_loads_list(row.pop("key_claims_json", "[]"))
    row["metadata"] = _json_loads_obj(row.pop("metadata_json", "{}"))
    return row


def _hydrate_event(row: dict[str, Any]) -> dict[str, Any]:
    row["video_ids"] = _json_loads_list(row.pop("video_ids_json", "[]"))
    row["channel_names"] = _json_loads_list(row.pop("channel_names_json", "[]"))
    row["metadata"] = _json_loads_obj(row.pop("metadata_json", "{}"))
    return row


def _clean_list(values: list[Any]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _json_loads_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _json_loads_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _parse_time(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
