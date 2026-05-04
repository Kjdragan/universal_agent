"""Intelligence-grade event emitter for non-gateway services.

Mission Control's tier-1 LLM card discovery reads `activity_events` rows
to surface "what just happened that's interesting" to the operator —
artifacts produced, reports delivered, missions completed, proactive
work that landed. The gateway has its own internal `_add_notification`
helper, but background workers (cron service, proactive pipelines,
outcome trackers) need a lightweight, dependency-free way to emit
events of their own.

This module provides exactly that. `emit_intelligence_event` opens the
activity DB, ensures schema, writes a record, and never raises — the
worflow it instruments must never break because the dashboard wasn't
listening.

Design contract:
  - Defensive: any exception is caught and logged. The caller's work is
    sacred; instrumentation must be best-effort.
  - Self-contained: no import of `gateway_server` (would be a circular
    dependency for the heartbeat/cron services that already feed it).
  - Schema-compatible: writes the same column shape the gateway reads,
    so the dashboard doesn't need a migration to surface these.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import sqlite3
from typing import Any
import uuid

logger = logging.getLogger(__name__)

# Severity vocabulary mirrors the tier-1 card severity bands so events
# and the cards synthesized from them speak the same language.
SEVERITY_INFO = "info"
SEVERITY_SUCCESS = "success"   # a meaningful work product was produced
SEVERITY_WATCHING = "watching"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

_VALID_SEVERITIES = {
    SEVERITY_INFO,
    SEVERITY_SUCCESS,
    SEVERITY_WATCHING,
    SEVERITY_WARNING,
    SEVERITY_CRITICAL,
}


def _activity_db_path() -> str:
    """Resolve the activity DB path the gateway uses. Lazy import to
    avoid pulling the heavy gateway/heartbeat module surface into
    workers that just want to write a single row."""
    from universal_agent.durable.db import get_activity_db_path
    return get_activity_db_path()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the activity_events table if it doesn't exist. CREATE
    TABLE IF NOT EXISTS is cheap; we run it on every emit so a service
    pointed at a fresh DB still gets the schema. (No process-level
    cache — that fails when tests use multiple tmp DBs.)"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS activity_events (
            id TEXT PRIMARY KEY,
            event_class TEXT NOT NULL DEFAULT 'notification',
            source_domain TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            full_message TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'new',
            requires_action INTEGER NOT NULL DEFAULT 0,
            session_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            entity_ref_json TEXT NOT NULL DEFAULT '{}',
            actions_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            channels_json TEXT NOT NULL DEFAULT '[]',
            email_targets_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_activity_events_created_at
            ON activity_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_source_domain
            ON activity_events(source_domain, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_kind
            ON activity_events(kind, created_at DESC);
        """
    )


def emit_intelligence_event(
    *,
    source_domain: str,
    kind: str,
    title: str,
    summary: str,
    severity: str = SEVERITY_INFO,
    metadata: dict[str, Any] | None = None,
    full_message: str | None = None,
    requires_action: bool = False,
    db_path: str | None = None,
) -> str | None:
    """Write an intelligence-grade event into the activity_events table.

    This is the canonical hook for "something interesting just
    happened" — a proactive report delivered, a cron job succeeded
    with a real artifact, a proactive task completed positively, an
    autonomous mission produced a noteworthy output.

    Returns the generated event_id on success, or None if the write
    failed (the caller should NOT depend on this — the function never
    raises, by contract).

    Caller examples:

        # Proactive intelligence report just landed
        emit_intelligence_event(
            source_domain="proactive_report",
            kind="intelligence_report_generated",
            title=f"Intelligence report ready ({period})",
            summary=f"3 recommendations, {n_signals} signals analyzed.",
            severity=SEVERITY_INFO,
            metadata={"report_id": report_id, "period": period},
        )

        # Cron job succeeded and produced an artifact
        emit_intelligence_event(
            source_domain="cron",
            kind="cron_job_success",
            title=f"Cron `{job_id}` completed",
            summary=f"Wrote {artifact_count} artifact(s) in {duration_s:.1f}s.",
            severity=SEVERITY_SUCCESS,
            metadata={"job_id": job_id, "duration_s": duration_s,
                      "artifacts": artifacts},
        )
    """
    if severity not in _VALID_SEVERITIES:
        logger.warning("emit_intelligence_event: unknown severity %r, "
                       "coercing to 'info'", severity)
        severity = SEVERITY_INFO

    event_id = f"{source_domain}_{kind}_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    full_msg = full_message if full_message is not None else summary

    try:
        path = db_path or _activity_db_path()
        conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO activity_events (
                    id, event_class, source_domain, kind, title, summary,
                    full_message, severity, status, requires_action,
                    session_id, created_at, updated_at,
                    entity_ref_json, actions_json, metadata_json,
                    channels_json, email_targets_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    "notification",
                    source_domain,
                    kind,
                    title,
                    summary,
                    full_msg,
                    severity,
                    "new",
                    1 if requires_action else 0,
                    None,
                    now_iso,
                    now_iso,
                    "{}",
                    "[]",
                    json.dumps(metadata or {}, default=str),
                    "[]",
                    "[]",
                ),
            )
        finally:
            conn.close()
        return event_id
    except Exception as exc:
        # Never break the caller's workflow because instrumentation
        # failed. Log loudly so operators can spot a sustained drop in
        # intelligence event rate.
        logger.warning(
            "emit_intelligence_event failed (source=%s kind=%s): %s",
            source_domain, kind, exc,
        )
        return None
