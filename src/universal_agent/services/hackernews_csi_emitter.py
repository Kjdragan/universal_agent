"""Emit `hackernews_movers_signal` events into the CSI events bus (Lane B, P2.B1).

Called from `build_snapshot()` at the end of each */30m HN snapshot
tick (P2.B2). For each "material" mover entry in the snapshot's
`movers.changes` array, write one event to the CSI `events` table at
`/var/lib/universal-agent/csi/csi.db`. Top-3 entries from
`snapshot.controversial` get one event each per day.

Materiality filter (§ 2.4 of the Phase 2 plan):
  - status="new" → always emit
  - status="moved" with |delta|>=3 → emit
  - status="dropped" with score>=200 → emit (sudden de-list of a
    high-scoring story can signal flag/quarantine)
  - controversial top-3 → one event each, regardless of mover state

Dedup: 24h via the `dedupe_keys` companion table, keyed by
`hn:<story_id>:<utc-yyyy-mm-dd>`. Same story can re-emit on a later
day's bucket.

Failure-tolerant by design: any DB error is logged + swallowed. The
caller (`build_snapshot()`) wraps the whole emit in a try/except so
emission never blocks the snapshot from being written.

See docs/integrations/hackernews_phase2_plan.md § 2 for the full
design and the CSI events table schema reference.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlparse
import uuid

logger = logging.getLogger(__name__)

# Canonical event-type and source strings. The csi_bridge.py whitelist
# (P2.B3) must be updated to include EVENT_TYPE for the events to flow
# into the `csi_recent_reports` tool.
EVENT_TYPE = "hackernews_movers_signal"
EVENT_SOURCE = "hackernews"

# Materiality thresholds — see § 2.4 of the Phase 2 plan.
DELTA_THRESHOLD = 3
DROP_SCORE_THRESHOLD = 200
CONTROVERSIAL_TOP_N = 3

# Dedup TTL — 24h day-bucket. Same story can re-emit on a later day.
DEDUP_TTL_HOURS = 24

# Default DB path. Override via CSI_DB_PATH env or csi_db_path arg.
_DEFAULT_CSI_DB_PATH = "/var/lib/universal-agent/csi/csi.db"

# SQLite lock-wait. csi.db sees writes from CSI_Ingester adapters too;
# 5s is the established convention.
SQLITE_BUSY_TIMEOUT_MS = 5000


def emit_movers_signals(
    snapshot: dict[str, Any] | None,
    *,
    csi_db_path: Path | str | None = None,
    now: datetime | None = None,
) -> int:
    """Emit movers + top-3-controversial signals to csi.db.

    Returns the number of events actually inserted (after dedup).
    Returns 0 — without raising — if the snapshot is missing/malformed,
    if the DB doesn't exist, or if the schema is unrecognizable.
    """
    if not isinstance(snapshot, dict):
        return 0

    db_path = Path(csi_db_path) if csi_db_path else Path(os.getenv("CSI_DB_PATH") or _DEFAULT_CSI_DB_PATH)
    if not db_path.exists():
        logger.info("HN CSI emitter: csi.db not found at %s; skipping", db_path)
        return 0

    now_utc = now or datetime.now(timezone.utc)
    candidates = list(_iter_candidates(snapshot))
    if not candidates:
        return 0

    watchlist = (snapshot.get("meta") or {}).get("watchlist") or []
    snapshot_generated_at = (snapshot.get("meta") or {}).get("generated_at") or ""

    try:
        return _write_signals(
            db_path=db_path,
            candidates=candidates,
            watchlist=list(watchlist),
            snapshot_generated_at=snapshot_generated_at,
            now=now_utc,
        )
    except sqlite3.DatabaseError as exc:
        logger.warning("HN CSI emitter: DB error: %s", exc)
        return 0


# ─── candidate iteration (materiality filter) ──────────────────────────


def _iter_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Yield candidate signal records ({source, change}) for material movers + top-3 controversial."""
    out: list[dict[str, Any]] = []

    movers = snapshot.get("movers") or {}
    if isinstance(movers, dict):
        for change in movers.get("changes") or []:
            if not isinstance(change, dict):
                continue
            if not _is_material(change):
                continue
            out.append({"category": "movers", "change": change})

    controversial = snapshot.get("controversial") or []
    if isinstance(controversial, list):
        for item in controversial[:CONTROVERSIAL_TOP_N]:
            if not isinstance(item, dict):
                continue
            out.append({"category": "controversial", "change": _controversial_to_change(item)})

    return out


def _is_material(change: dict[str, Any]) -> bool:
    """Apply the materiality filter from § 2.4."""
    status = (change.get("status") or "").lower()
    try:
        delta = abs(int(change.get("delta") or 0))
        score = int(change.get("score") or 0)
    except (TypeError, ValueError):
        return False
    if status == "new":
        return True
    if status == "moved" and delta >= DELTA_THRESHOLD:
        return True
    if status == "dropped" and score >= DROP_SCORE_THRESHOLD:
        return True
    return False


def _controversial_to_change(item: dict[str, Any]) -> dict[str, Any]:
    """Coerce a `controversial[]` entry into the same shape as a movers `change`."""
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "url": item.get("url"),
        "status": "controversial",
        "rank": 0,
        "score": int(item.get("score") or 0),
        "delta": 0,
        "descendants": int(item.get("descendants") or 0),
        "ratio": item.get("ratio"),
    }


# ─── DB writes (dedup + insert) ────────────────────────────────────────


def _write_signals(
    *,
    db_path: Path,
    candidates: list[dict[str, Any]],
    watchlist: list[str],
    snapshot_generated_at: str,
    now: datetime,
) -> int:
    """Open the DB, write signals (with dedup), return inserted count."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        if not _has_required_schema(conn):
            logger.warning("HN CSI emitter: csi.db is missing events/dedupe_keys; skipping")
            return 0

        day_bucket = now.strftime("%Y-%m-%d")
        ttl_expires = (now + _ttl_delta()).isoformat()
        inserted = 0

        for cand in candidates:
            change = cand.get("change") or {}
            sid = change.get("id")
            if sid is None:
                continue
            try:
                if _try_dedup_insert(conn, sid, day_bucket, ttl_expires):
                    _insert_event(
                        conn,
                        sid=sid,
                        change=change,
                        category=str(cand.get("category") or ""),
                        watchlist=watchlist,
                        snapshot_generated_at=snapshot_generated_at,
                        day_bucket=day_bucket,
                        now=now,
                    )
                    inserted += 1
            except sqlite3.DatabaseError as exc:
                # One bad row mustn't kill the rest — log and continue.
                logger.warning("HN CSI emitter: insert failed for sid=%s: %s", sid, exc)
                conn.rollback()

        conn.commit()
        return inserted
    finally:
        conn.close()


def _has_required_schema(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('events', 'dedupe_keys')"
    ).fetchall()
    names = {r[0] for r in rows}
    return {"events", "dedupe_keys"}.issubset(names)


def _try_dedup_insert(conn: sqlite3.Connection, sid: Any, day_bucket: str, ttl_expires: str) -> bool:
    """Insert into dedupe_keys with INSERT OR IGNORE. Returns True if this was a new key."""
    key = f"hn:{sid}:{day_bucket}"
    cursor = conn.execute(
        "INSERT OR IGNORE INTO dedupe_keys (key, expires_at) VALUES (?, ?)",
        (key, ttl_expires),
    )
    return cursor.rowcount > 0


def _insert_event(
    conn: sqlite3.Connection,
    *,
    sid: Any,
    change: dict[str, Any],
    category: str,
    watchlist: list[str],
    snapshot_generated_at: str,
    day_bucket: str,
    now: datetime,
) -> None:
    occurred_at = now.isoformat()
    received_at = occurred_at
    event_id = f"hn:{sid}:{now.strftime('%Y%m%dT%H%M%SZ')}:{uuid.uuid4().hex[:8]}"
    dedupe_key = f"hn:{sid}:{day_bucket}"

    title = change.get("title") or f"#{sid}"
    url = change.get("url") or ""
    host = ""
    if url:
        try:
            host = urlparse(url).hostname or ""
            host = host.removeprefix("www.")
        except Exception:  # noqa: BLE001
            host = ""

    subject = {
        "story_id": sid,
        "title": title,
        "url": url,
        "host": host,
        "score": int(change.get("score") or 0),
        "descendants": int(change.get("descendants") or 0),
        "rank": int(change.get("rank") or 0),
        "movement": {
            "status": str(change.get("status") or ""),
            "delta": int(change.get("delta") or 0),
            "ratio_cmt_pts": change.get("ratio"),
        },
        "comment_url": f"https://news.ycombinator.com/item?id={sid}",
        "topic_match": _topic_match(title, watchlist),
    }
    routing = {"lane": "hackernews", "category": category or "movers"}
    metadata = {"snapshot_generated_at": snapshot_generated_at}

    conn.execute(
        """
        INSERT INTO events (
            event_id, dedupe_key, source, event_type,
            occurred_at, received_at, subject_json, routing_json, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            dedupe_key,
            EVENT_SOURCE,
            EVENT_TYPE,
            occurred_at,
            received_at,
            json.dumps(subject, ensure_ascii=False),
            json.dumps(routing, ensure_ascii=False),
            json.dumps(metadata, ensure_ascii=False),
        ),
    )


def _topic_match(title: str, watchlist: list[str]) -> list[str]:
    """Case-insensitive substring match of watchlist topics against the title."""
    if not title:
        return []
    title_lower = title.lower()
    return [t for t in watchlist if isinstance(t, str) and t.lower() in title_lower]


def _ttl_delta():
    from datetime import timedelta
    return timedelta(hours=DEDUP_TTL_HOURS)
