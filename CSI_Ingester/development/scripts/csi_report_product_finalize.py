#!/usr/bin/env python3
"""Materialize CSI report product artifacts and emit readiness signal to UA."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store.sqlite import connect, ensure_schema


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _latest_trend_report(conn: sqlite3.Connection, window_hours: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT report_key, window_start_utc, window_end_utc, report_markdown, report_json, created_at
        FROM trend_reports
        WHERE created_at >= datetime('now', ?)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (f"-{max(1, int(window_hours))} hours",),
    ).fetchone()
    if not row:
        return {}
    out = {key: row[key] for key in row.keys()}
    try:
        out["report_json"] = json.loads(str(out.get("report_json") or "{}"))
    except Exception:
        out["report_json"] = {}
    return out


def _latest_insight_reports(conn: sqlite3.Connection, window_hours: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT report_key, report_type, window_start_utc, window_end_utc, report_markdown, report_json, created_at
        FROM insight_reports
        WHERE created_at >= datetime('now', ?)
        ORDER BY created_at DESC, id DESC
        LIMIT 6
        """,
        (f"-{max(1, int(window_hours))} hours",),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        try:
            item["report_json"] = json.loads(str(item.get("report_json") or "{}"))
        except Exception:
            item["report_json"] = {}
        out.append(item)
    return out


def _token_snapshot(conn: sqlite3.Connection, window_hours: int) -> dict[str, Any]:
    totals_row = conn.execute(
        """
        SELECT
            COUNT(*) AS records,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= datetime('now', ?)
        """,
        (f"-{max(1, int(window_hours))} hours",),
    ).fetchone()

    process_rows = conn.execute(
        """
        SELECT process_name, COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
        WHERE occurred_at >= datetime('now', ?)
        GROUP BY process_name
        ORDER BY total_tokens DESC
        LIMIT 12
        """,
        (f"-{max(1, int(window_hours))} hours",),
    ).fetchall()

    return {
        "records": int(totals_row["records"] or 0),
        "prompt_tokens": int(totals_row["prompt_tokens"] or 0),
        "completion_tokens": int(totals_row["completion_tokens"] or 0),
        "total_tokens": int(totals_row["total_tokens"] or 0),
        "top_processes": [
            {"process_name": str(row["process_name"] or ""), "total_tokens": int(row["total_tokens"] or 0)}
            for row in process_rows
        ],
    }


def _build_markdown(
    *,
    now_iso: str,
    window_hours: int,
    trend_report: dict[str, Any],
    insight_reports: list[dict[str, Any]],
    token_snapshot: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("# CSI Report Product")
    lines.append("")
    lines.append(f"- generated_at_utc: {now_iso}")
    lines.append(f"- coverage_window_hours: {int(window_hours)}")
    lines.append("")

    lines.append("## Token Usage Snapshot")
    lines.append(
        f"- total_tokens={int(token_snapshot.get('total_tokens') or 0)} "
        f"(prompt={int(token_snapshot.get('prompt_tokens') or 0)}, completion={int(token_snapshot.get('completion_tokens') or 0)})"
    )
    lines.append(f"- records={int(token_snapshot.get('records') or 0)}")
    for item in token_snapshot.get("top_processes", [])[:8]:
        lines.append(f"- {item['process_name']}: {int(item['total_tokens'])}")
    lines.append("")

    lines.append("## Latest Trend Report")
    if trend_report:
        lines.append(f"- report_key: {str(trend_report.get('report_key') or '')}")
        lines.append(f"- window: {str(trend_report.get('window_start_utc') or '')} -> {str(trend_report.get('window_end_utc') or '')}")
        trend_json = trend_report.get("report_json") if isinstance(trend_report.get("report_json"), dict) else {}
        top_channels = trend_json.get("top_channels") if isinstance(trend_json.get("top_channels"), list) else []
        top_themes = trend_json.get("top_themes") if isinstance(trend_json.get("top_themes"), list) else []
        if top_channels:
            lines.append("- top_channels:")
            for channel in top_channels[:5]:
                lines.append(f"  - {json.dumps(channel, ensure_ascii=False)}")
        if top_themes:
            lines.append("- top_themes:")
            for theme in top_themes[:6]:
                lines.append(f"  - {json.dumps(theme, ensure_ascii=False)}")
    else:
        lines.append("- no trend report in window")
    lines.append("")

    lines.append("## Latest Insight Reports")
    if insight_reports:
        for report in insight_reports[:4]:
            lines.append(
                "- "
                f"{str(report.get('report_type') or '')}: "
                f"{str(report.get('window_start_utc') or '')} -> {str(report.get('window_end_utc') or '')} "
                f"(key={str(report.get('report_key') or '')})"
            )
    else:
        lines.append("- no insight reports in window")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize hourly CSI report product and emit UA readiness event.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--output-root", default="/opt/universal_agent/artifacts/csi-reports")
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/rss_report_product_state.json",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)
    config = load_config()

    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y%m%d%H")
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    state_path = Path(args.state_path).expanduser()
    state = _load_state(state_path)
    if not args.force and str(state.get("last_hour_key") or "") == hour_key:
        print(f"CSI_REPORT_PRODUCT_SKIPPED=already_sent_hour:{hour_key}")
        conn.close()
        return 0

    trend_report = _latest_trend_report(conn, max(1, int(args.window_hours)))
    insight_reports = _latest_insight_reports(conn, max(1, int(args.window_hours)))
    token_snapshot = _token_snapshot(conn, max(1, int(args.window_hours)))

    day = now.strftime("%Y-%m-%d")
    output_dir = Path(args.output_root).expanduser() / day / "product"
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"hourly_{hour_key}"
    md_path = output_dir / f"{base_name}.md"
    json_path = output_dir / f"{base_name}.json"

    markdown = _build_markdown(
        now_iso=now_iso,
        window_hours=max(1, int(args.window_hours)),
        trend_report=trend_report,
        insight_reports=insight_reports,
        token_snapshot=token_snapshot,
    )
    payload = {
        "generated_at_utc": now_iso,
        "window_hours": max(1, int(args.window_hours)),
        "trend_report": trend_report,
        "insight_reports": insight_reports,
        "token_snapshot": token_snapshot,
        "markdown_path": str(md_path),
    }

    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    event = CreatorSignalEvent(
        event_id=f"csi:report_product:{config.instance_id}:{hour_key}",
        dedupe_key=f"csi:report_product:{config.instance_id}:{hour_key}",
        source="csi_analytics",
        event_type="report_product_ready",
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "report_type": "hourly_report_product",
            "hour_key": hour_key,
            "generated_at_utc": now_iso,
            "window_hours": max(1, int(args.window_hours)),
            "artifact_paths": {
                "markdown": str(md_path),
                "json": str(json_path),
            },
            "token_snapshot": token_snapshot,
            "has_trend_report": bool(trend_report),
            "insight_report_count": len(insight_reports),
        },
        routing={
            "pipeline": "csi_report_product",
            "priority": "standard",
            "tags": ["csi", "report", "product", "hourly"],
        },
        metadata={"source_adapter": "csi_report_product_finalize_v1"},
    )
    delivered, status_code, _resp = emit_and_track(conn, config=config, event=event, retry_count=3)
    conn.close()

    _save_state(
        state_path,
        {
            "last_hour_key": hour_key,
            "last_sent_at": now_iso,
            "last_status_code": int(status_code),
            "last_delivered": bool(delivered),
            "markdown": str(md_path),
            "json": str(json_path),
        },
    )

    print(f"CSI_REPORT_PRODUCT_HOUR={hour_key}")
    print(f"CSI_REPORT_PRODUCT_MD={md_path}")
    print(f"CSI_REPORT_PRODUCT_JSON={json_path}")
    print(f"CSI_REPORT_PRODUCT_EMIT_STATUS={status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
