"""Event persistence."""

from __future__ import annotations

import json
import sqlite3

from csi_ingester.contract import CreatorSignalEvent


def insert_event(conn: sqlite3.Connection, event: CreatorSignalEvent) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO events (
            event_id, dedupe_key, source, event_type, occurred_at, received_at, emitted_at,
            subject_json, routing_json, metadata_json, delivered
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.dedupe_key,
            event.source,
            event.event_type,
            event.occurred_at,
            event.received_at,
            event.emitted_at,
            json.dumps(event.subject, separators=(",", ":"), sort_keys=True),
            json.dumps(event.routing, separators=(",", ":"), sort_keys=True),
            json.dumps(event.metadata, separators=(",", ":"), sort_keys=True),
            0,
        ),
    )
    conn.commit()


def mark_delivered(conn: sqlite3.Connection, event_id: str) -> None:
    conn.execute("UPDATE events SET delivered = 1, emitted_at = datetime('now') WHERE event_id = ?", (event_id,))
    conn.commit()
