"""Durable inventory for proactive work products.

This module stores work products that agents create without a direct user
request. Task Hub remains the execution queue; proactive artifacts are reviewable
inventory with feedback and delivery lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

ARTIFACT_STATUS_PRODUCED = "produced"
ARTIFACT_STATUS_CANDIDATE = "candidate"
ARTIFACT_STATUS_SURFACED = "surfaced"
ARTIFACT_STATUS_ACCEPTED = "accepted"
ARTIFACT_STATUS_REJECTED = "rejected"
ARTIFACT_STATUS_ARCHIVED = "archived"

VALID_ARTIFACT_STATUSES = {
    ARTIFACT_STATUS_PRODUCED,
    ARTIFACT_STATUS_CANDIDATE,
    ARTIFACT_STATUS_SURFACED,
    ARTIFACT_STATUS_ACCEPTED,
    ARTIFACT_STATUS_REJECTED,
    ARTIFACT_STATUS_ARCHIVED,
}

DELIVERY_NOT_SURFACED = "not_surfaced"
DELIVERY_DIGEST_QUEUED = "digest_queued"
DELIVERY_EMAILED = "emailed"
DELIVERY_EMAIL_FAILED = "email_failed"
DELIVERY_REVIEWED = "reviewed"

VALID_DELIVERY_STATES = {
    DELIVERY_NOT_SURFACED,
    DELIVERY_DIGEST_QUEUED,
    DELIVERY_EMAILED,
    DELIVERY_EMAIL_FAILED,
    DELIVERY_REVIEWED,
}

_ARTIFACT_ID_RE = re.compile(r"\bpa_[a-f0-9]{16}\b", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


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


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in text.split(",") if part.strip()]
    return []


def make_artifact_id(*, source_kind: str, source_ref: str, artifact_type: str, title: str = "") -> str:
    seed = "|".join(
        [
            str(source_kind or "").strip().lower(),
            str(source_ref or "").strip(),
            str(artifact_type or "").strip().lower(),
            str(title or "").strip().lower(),
        ]
    )
    return f"pa_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proactive_artifacts (
            artifact_id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_ref TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'produced',
            delivery_state TEXT NOT NULL DEFAULT 'not_surfaced',
            priority INTEGER NOT NULL DEFAULT 2,
            artifact_uri TEXT NOT NULL DEFAULT '',
            artifact_path TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            topic_tags_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            feedback_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            surfaced_at TEXT NOT NULL DEFAULT '',
            accepted_at TEXT NOT NULL DEFAULT '',
            rejected_at TEXT NOT NULL DEFAULT '',
            archived_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_proactive_artifacts_status
            ON proactive_artifacts(status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_proactive_artifacts_delivery
            ON proactive_artifacts(delivery_state, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_proactive_artifacts_source
            ON proactive_artifacts(source_kind, source_ref);

        CREATE TABLE IF NOT EXISTS proactive_artifact_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            thread_id TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            recipient TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL,
            delivery_state TEXT NOT NULL DEFAULT 'emailed',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_artifact_emails_thread
            ON proactive_artifact_emails(thread_id);
        CREATE INDEX IF NOT EXISTS idx_proactive_artifact_emails_message
            ON proactive_artifact_emails(message_id);
        CREATE INDEX IF NOT EXISTS idx_proactive_artifact_emails_artifact
            ON proactive_artifact_emails(artifact_id, sent_at DESC);

        CREATE TABLE IF NOT EXISTS proactive_artifact_feedback (
            feedback_id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL,
            score INTEGER,
            text TEXT NOT NULL DEFAULT '',
            raw_reply TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT 'kevin',
            thread_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_artifact_feedback_artifact
            ON proactive_artifact_feedback(artifact_id, created_at DESC);
        """
    )
    conn.commit()


def upsert_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_id: str = "",
    artifact_type: str,
    source_kind: str,
    title: str,
    source_ref: str = "",
    summary: str = "",
    status: str = ARTIFACT_STATUS_PRODUCED,
    delivery_state: str = DELIVERY_NOT_SURFACED,
    priority: int = 2,
    artifact_uri: str = "",
    artifact_path: str = "",
    source_url: str = "",
    topic_tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    normalized_status = status if status in VALID_ARTIFACT_STATUSES else ARTIFACT_STATUS_PRODUCED
    normalized_delivery = delivery_state if delivery_state in VALID_DELIVERY_STATES else DELIVERY_NOT_SURFACED
    clean_id = str(artifact_id or "").strip() or make_artifact_id(
        source_kind=source_kind,
        source_ref=source_ref,
        artifact_type=artifact_type,
        title=title,
    )
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO proactive_artifacts (
            artifact_id, artifact_type, source_kind, source_ref, title, summary,
            status, delivery_state, priority, artifact_uri, artifact_path, source_url,
            topic_tags_json, metadata_json, feedback_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
        ON CONFLICT(artifact_id) DO UPDATE SET
            artifact_type=excluded.artifact_type,
            source_kind=excluded.source_kind,
            source_ref=excluded.source_ref,
            title=excluded.title,
            summary=excluded.summary,
            status=excluded.status,
            delivery_state=excluded.delivery_state,
            priority=excluded.priority,
            artifact_uri=excluded.artifact_uri,
            artifact_path=excluded.artifact_path,
            source_url=excluded.source_url,
            topic_tags_json=excluded.topic_tags_json,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            clean_id,
            str(artifact_type or "").strip() or "artifact",
            str(source_kind or "").strip() or "unknown",
            str(source_ref or "").strip(),
            str(title or "").strip() or "(untitled proactive artifact)",
            str(summary or "").strip(),
            normalized_status,
            normalized_delivery,
            max(0, int(priority or 0)),
            str(artifact_uri or "").strip(),
            str(artifact_path or "").strip(),
            str(source_url or "").strip(),
            _json_dumps(_normalize_list(topic_tags or [])),
            _json_dumps(metadata or {}),
            now,
            now,
        ),
    )
    conn.commit()
    return get_artifact(conn, clean_id) or {}


def get_artifact(conn: sqlite3.Connection, artifact_id: str) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM proactive_artifacts WHERE artifact_id = ? LIMIT 1",
        (str(artifact_id or "").strip(),),
    ).fetchone()
    return _hydrate_artifact(dict(row)) if row else None


def list_artifacts(
    conn: sqlite3.Connection,
    *,
    status: str = "",
    delivery_state: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_schema(conn)
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if delivery_state:
        clauses.append("delivery_state = ?")
        params.append(delivery_state)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM proactive_artifacts
        {where}
        ORDER BY priority DESC, updated_at DESC
        LIMIT ?
        """,
        (*params, max(1, min(int(limit), 500))),
    ).fetchall()
    return [_hydrate_artifact(dict(row)) for row in rows]


def sync_from_proactive_signal_cards(conn: sqlite3.Connection, *, limit: int = 200) -> dict[str, int]:
    """Create proactive artifact inventory rows from existing signal cards."""
    ensure_schema(conn)
    try:
        from universal_agent import proactive_signals

        cards = proactive_signals.list_cards(conn, limit=max(1, min(int(limit), 500)))
    except Exception:
        return {"seen": 0, "upserted": 0}

    upserted = 0
    for card in cards:
        try:
            upsert_from_proactive_signal_card(conn, card)
            upserted += 1
        except ValueError:
            continue
    return {"seen": len(cards), "upserted": upserted}


def upsert_from_proactive_signal_card(conn: sqlite3.Connection, card: dict[str, Any]) -> dict[str, Any]:
    """Create/update a proactive artifact row for one proactive signal card."""
    card_id = str(card.get("card_id") or "").strip()
    if not card_id:
        raise ValueError("card_id is required")
    evidence = card.get("evidence") if isinstance(card.get("evidence"), list) else []
    source_url = _first_evidence_url(evidence)
    return upsert_artifact(
        conn,
        artifact_id=make_artifact_id(
            source_kind="proactive_signal",
            source_ref=card_id,
            artifact_type=str(card.get("card_type") or "signal_card"),
            title=str(card.get("title") or ""),
        ),
        artifact_type=str(card.get("card_type") or "signal_card"),
        source_kind="proactive_signal",
        source_ref=card_id,
        title=str(card.get("title") or ""),
        summary=str(card.get("summary") or ""),
        status=ARTIFACT_STATUS_CANDIDATE,
        priority=int(card.get("priority") or 2),
        source_url=source_url,
        topic_tags=[str(card.get("source") or ""), str(card.get("card_type") or "")],
        metadata={"proactive_signal_card_id": card_id, "card_status": card.get("status")},
    )


def update_artifact_state(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    status: str | None = None,
    delivery_state: str | None = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    current = get_artifact(conn, artifact_id)
    if current is None:
        raise KeyError(artifact_id)
    new_status = status if status in VALID_ARTIFACT_STATUSES else current["status"]
    new_delivery = delivery_state if delivery_state in VALID_DELIVERY_STATES else current["delivery_state"]
    now = _now_iso()
    timestamp_updates = {
        "surfaced_at": now if new_status == ARTIFACT_STATUS_SURFACED else current.get("surfaced_at", ""),
        "accepted_at": now if new_status == ARTIFACT_STATUS_ACCEPTED else current.get("accepted_at", ""),
        "rejected_at": now if new_status == ARTIFACT_STATUS_REJECTED else current.get("rejected_at", ""),
        "archived_at": now if new_status == ARTIFACT_STATUS_ARCHIVED else current.get("archived_at", ""),
    }
    conn.execute(
        """
        UPDATE proactive_artifacts
        SET status = ?, delivery_state = ?, updated_at = ?,
            surfaced_at = ?, accepted_at = ?, rejected_at = ?, archived_at = ?
        WHERE artifact_id = ?
        """,
        (
            new_status,
            new_delivery,
            now,
            timestamp_updates["surfaced_at"],
            timestamp_updates["accepted_at"],
            timestamp_updates["rejected_at"],
            timestamp_updates["archived_at"],
            artifact_id,
        ),
    )
    conn.commit()
    return get_artifact(conn, artifact_id) or {}


def record_email_delivery(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    message_id: str = "",
    thread_id: str = "",
    subject: str = "",
    recipient: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    if get_artifact(conn, artifact_id) is None:
        raise KeyError(artifact_id)
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO proactive_artifact_emails (
            artifact_id, message_id, thread_id, subject, recipient, sent_at,
            delivery_state, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            str(message_id or "").strip(),
            str(thread_id or "").strip(),
            str(subject or "").strip(),
            str(recipient or "").strip(),
            now,
            DELIVERY_EMAILED,
            _json_dumps(metadata or {}),
        ),
    )
    update_artifact_state(
        conn,
        artifact_id=artifact_id,
        status=ARTIFACT_STATUS_SURFACED,
        delivery_state=DELIVERY_EMAILED,
    )
    return get_artifact(conn, artifact_id) or {}


def find_artifact_for_reply(
    conn: sqlite3.Connection,
    *,
    subject: str = "",
    thread_id: str = "",
    message_id: str = "",
) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    clean_thread = str(thread_id or "").strip()
    if clean_thread:
        row = conn.execute(
            """
            SELECT artifact_id
            FROM proactive_artifact_emails
            WHERE thread_id = ?
            ORDER BY sent_at DESC
            LIMIT 1
            """,
            (clean_thread,),
        ).fetchone()
        if row:
            return get_artifact(conn, str(row["artifact_id"]))

    clean_message = str(message_id or "").strip()
    if clean_message:
        row = conn.execute(
            """
            SELECT artifact_id
            FROM proactive_artifact_emails
            WHERE message_id = ?
            ORDER BY sent_at DESC
            LIMIT 1
            """,
            (clean_message,),
        ).fetchone()
        if row:
            return get_artifact(conn, str(row["artifact_id"]))

    match = _ARTIFACT_ID_RE.search(str(subject or ""))
    if match:
        return get_artifact(conn, match.group(0).lower())
    return None


def record_feedback(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    score: int | None = None,
    text: str = "",
    raw_reply: str = "",
    actor: str = "kevin",
    thread_id: str = "",
    message_id: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    artifact = get_artifact(conn, artifact_id)
    if artifact is None:
        raise KeyError(artifact_id)
    normalized_score = int(score) if score is not None and 1 <= int(score) <= 5 else None
    created_at = _now_iso()
    feedback_id = "pf_" + hashlib.sha256(
        "|".join([artifact_id, str(thread_id), str(message_id), created_at]).encode()
    ).hexdigest()[:16]
    conn.execute(
        """
        INSERT INTO proactive_artifact_feedback (
            feedback_id, artifact_id, score, text, raw_reply, actor, thread_id,
            message_id, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feedback_id,
            artifact_id,
            normalized_score,
            str(text or "").strip(),
            str(raw_reply or "").strip(),
            str(actor or "kevin").strip() or "kevin",
            str(thread_id or "").strip(),
            str(message_id or "").strip(),
            created_at,
            _json_dumps(metadata or {}),
        ),
    )
    feedback = dict(artifact.get("feedback") or {})
    history = feedback.get("history") if isinstance(feedback.get("history"), list) else []
    history.append(
        {
            "feedback_id": feedback_id,
            "score": normalized_score,
            "text": str(text or "").strip(),
            "actor": str(actor or "kevin").strip() or "kevin",
            "created_at": created_at,
        }
    )
    feedback["history"] = history[-100:]
    feedback["last_score"] = normalized_score
    feedback["last_feedback_at"] = created_at
    conn.execute(
        """
        UPDATE proactive_artifacts
        SET feedback_json = ?, delivery_state = ?, updated_at = ?
        WHERE artifact_id = ?
        """,
        (_json_dumps(feedback), DELIVERY_REVIEWED, created_at, artifact_id),
    )
    if normalized_score in {1, 5}:
        update_artifact_state(conn, artifact_id=artifact_id, status=ARTIFACT_STATUS_ACCEPTED, delivery_state=DELIVERY_REVIEWED)
    elif normalized_score in {3, 4}:
        update_artifact_state(conn, artifact_id=artifact_id, status=ARTIFACT_STATUS_REJECTED, delivery_state=DELIVERY_REVIEWED)
    else:
        update_artifact_state(conn, artifact_id=artifact_id, delivery_state=DELIVERY_REVIEWED)
    return get_artifact(conn, artifact_id) or {}


def _hydrate_artifact(row: dict[str, Any]) -> dict[str, Any]:
    row["topic_tags"] = _normalize_list(row.pop("topic_tags_json", "[]"))
    row["metadata"] = _json_loads_obj(row.pop("metadata_json", "{}"))
    row["feedback"] = _json_loads_obj(row.pop("feedback_json", "{}"))
    return row


def _first_evidence_url(evidence: list[Any]) -> str:
    for item in evidence:
        if not isinstance(item, dict):
            continue
        for key in ("url", "href", "link", "source_url"):
            value = str(item.get(key) or "").strip()
            if value.startswith(("http://", "https://")):
                return value
    return ""
