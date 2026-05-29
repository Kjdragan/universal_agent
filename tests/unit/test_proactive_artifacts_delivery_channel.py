"""Guards that proactive_artifacts carries the delivery_channel column.

The hourly-intel-digest throttle (`hourly_intel_digest.is_throttled`) queries
``WHERE delivery_channel = 'hourly_digest'``. PR B added verdict /
verdict_reasoning to the canonical schema but omitted delivery_channel, so on
the production DB the column was absent and the digest would have errored on
its first real candidate. See
docs/proactive_signals/insight_pipeline_remediation_plan_2026-05-28.md.
"""
from __future__ import annotations

import sqlite3

from universal_agent.services import proactive_artifacts


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {
        row[1]
        for row in conn.execute("PRAGMA table_info(proactive_artifacts)").fetchall()
    }


def test_ensure_schema_adds_delivery_channel_column():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        proactive_artifacts.ensure_schema(conn)
        cols = _columns(conn)
        assert "delivery_channel" in cols
        # The siblings PR B added should still be present too.
        assert "verdict" in cols
        assert "verdict_reasoning" in cols
    finally:
        conn.close()


def test_ensure_schema_is_idempotent_on_delivery_channel():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        proactive_artifacts.ensure_schema(conn)
        # Second call must not raise (duplicate-column ALTER is swallowed).
        proactive_artifacts.ensure_schema(conn)
        assert "delivery_channel" in _columns(conn)
    finally:
        conn.close()
