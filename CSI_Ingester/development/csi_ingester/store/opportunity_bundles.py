"""Persistence helpers for CSI opportunity bundles."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def upsert_bundle(
    conn: sqlite3.Connection,
    *,
    bundle_id: str,
    report_key: str,
    window_start_utc: str,
    window_end_utc: str,
    confidence_method: str,
    quality_summary: dict[str, Any],
    opportunities: list[dict[str, Any]],
    artifact_markdown_path: str,
    artifact_json_path: str,
) -> None:
    conn.execute(
        """
        INSERT INTO opportunity_bundles (
            bundle_id,
            report_key,
            window_start_utc,
            window_end_utc,
            confidence_method,
            quality_summary_json,
            opportunities_json,
            artifact_markdown_path,
            artifact_json_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bundle_id) DO UPDATE SET
            report_key=excluded.report_key,
            window_start_utc=excluded.window_start_utc,
            window_end_utc=excluded.window_end_utc,
            confidence_method=excluded.confidence_method,
            quality_summary_json=excluded.quality_summary_json,
            opportunities_json=excluded.opportunities_json,
            artifact_markdown_path=excluded.artifact_markdown_path,
            artifact_json_path=excluded.artifact_json_path,
            created_at=datetime('now')
        """,
        (
            str(bundle_id or "").strip(),
            str(report_key or "").strip() or None,
            str(window_start_utc or "").strip(),
            str(window_end_utc or "").strip(),
            str(confidence_method or "heuristic").strip().lower(),
            json.dumps(quality_summary if isinstance(quality_summary, dict) else {}, separators=(",", ":"), sort_keys=True),
            json.dumps(opportunities if isinstance(opportunities, list) else [], separators=(",", ":"), sort_keys=True),
            str(artifact_markdown_path or "").strip() or None,
            str(artifact_json_path or "").strip() or None,
        ),
    )
    conn.commit()


def decode_bundle_row(row: sqlite3.Row) -> dict[str, Any]:
    quality_summary: dict[str, Any] = {}
    opportunities: list[dict[str, Any]] = []
    try:
        payload = json.loads(str(row["quality_summary_json"] or "{}"))
        if isinstance(payload, dict):
            quality_summary = payload
    except Exception:
        quality_summary = {}
    try:
        payload = json.loads(str(row["opportunities_json"] or "[]"))
        if isinstance(payload, list):
            opportunities = [item for item in payload if isinstance(item, dict)]
    except Exception:
        opportunities = []
    return {
        "bundle_id": str(row["bundle_id"] or ""),
        "report_key": str(row["report_key"] or ""),
        "window_start_utc": str(row["window_start_utc"] or ""),
        "window_end_utc": str(row["window_end_utc"] or ""),
        "confidence_method": str(row["confidence_method"] or "heuristic"),
        "quality_summary": quality_summary,
        "opportunities": opportunities,
        "artifact_paths": {
            "markdown": str(row["artifact_markdown_path"] or ""),
            "json": str(row["artifact_json_path"] or ""),
        },
        "created_at": str(row["created_at"] or ""),
    }
