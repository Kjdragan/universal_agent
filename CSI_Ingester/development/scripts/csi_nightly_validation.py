#!/usr/bin/env python3
"""Run nightly CSI end-to-end validation checks and emit a validation event."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.llm_auth import resolve_csi_llm_auth
from csi_ingester.store.sqlite import connect, ensure_schema


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, raw = item.split("=", 1)
        val = raw.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _runtime_db_path_from_env() -> Path:
    raw = (os.getenv("UA_RUNTIME_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path("/opt/universal_agent/AGENT_RUN_WORKSPACES/runtime_state.db")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    metrics: dict[str, Any]


def _check_auth_lane(env_values: dict[str, str], require_mode1: bool) -> CheckResult:
    try:
        settings = resolve_csi_llm_auth(env_values, default_base_url="https://api.anthropic.com")
    except Exception as exc:
        return CheckResult(
            name="auth_lane",
            ok=False,
            detail=f"Auth resolver failed: {exc}",
            metrics={},
        )
    if require_mode1 and settings.mode != 1:
        return CheckResult(
            name="auth_lane",
            ok=False,
            detail="Expected CSI_LLM_AUTH_MODE=1 for dedicated-lane validation.",
            metrics={"mode": settings.mode, "lane": settings.lane},
        )
    return CheckResult(
        name="auth_lane",
        ok=bool(settings.api_key),
        detail="Auth lane resolved.",
        metrics={"mode": settings.mode, "lane": settings.lane, "base_url": settings.base_url},
    )


def _check_csi_db(csi_db: Path, lookback_hours: int) -> CheckResult:
    if not csi_db.exists():
        return CheckResult("csi_db", False, f"CSI DB missing: {csi_db}", {})
    conn = connect(csi_db)
    ensure_schema(conn)
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))).isoformat()
        total_events = int(
            conn.execute("SELECT COUNT(*) AS c FROM events WHERE occurred_at >= ?", (since,)).fetchone()["c"] or 0
        )
        rss_events = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE occurred_at >= ? AND source = 'youtube_channel_rss'",
                (since,),
            ).fetchone()["c"]
            or 0
        )
        reddit_events = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE occurred_at >= ? AND source = 'reddit_discovery'",
                (since,),
            ).fetchone()["c"]
            or 0
        )
        insight_reports = int(
            conn.execute("SELECT COUNT(*) AS c FROM insight_reports WHERE created_at >= ?", (since,)).fetchone()["c"] or 0
        )
        trend_reports = int(
            conn.execute("SELECT COUNT(*) AS c FROM trend_reports WHERE created_at >= ?", (since,)).fetchone()["c"] or 0
        )
        ok = total_events > 0 and insight_reports > 0 and trend_reports > 0 and reddit_events > 0
        detail = "CSI database checks passed." if ok else "CSI database checks failed minimum thresholds."
        return CheckResult(
            "csi_db",
            ok,
            detail,
            {
                "lookback_hours": lookback_hours,
                "total_events": total_events,
                "rss_events": rss_events,
                "reddit_events": reddit_events,
                "insight_reports": insight_reports,
                "trend_reports": trend_reports,
            },
        )
    finally:
        conn.close()


def _check_runtime_activity(runtime_db: Path, lookback_hours: int) -> CheckResult:
    if not runtime_db.exists():
        return CheckResult("runtime_activity", False, f"Runtime DB missing: {runtime_db}", {})
    conn = sqlite3.connect(str(runtime_db))
    conn.row_factory = sqlite3.Row
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))).isoformat()
        has_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_events'"
        ).fetchone()
        if has_table is None:
            return CheckResult("runtime_activity", False, "activity_events table missing", {})
        csi_events = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM activity_events WHERE created_at >= ? AND source_domain = 'csi'",
                (since,),
            ).fetchone()["c"]
            or 0
        )
        specialist_hourly = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM activity_events WHERE created_at >= ? AND kind = 'csi_specialist_hourly_synthesis'",
                (since,),
            ).fetchone()["c"]
            or 0
        )
        specialist_daily = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM activity_events WHERE created_at >= ? AND kind = 'csi_specialist_daily_rollup'",
                (since,),
            ).fetchone()["c"]
            or 0
        )
        ok = csi_events > 0 and (specialist_hourly > 0 or specialist_daily > 0)
        detail = "Runtime activity checks passed." if ok else "Runtime activity missing CSI/specialist signals."
        return CheckResult(
            "runtime_activity",
            ok,
            detail,
            {
                "lookback_hours": lookback_hours,
                "csi_events": csi_events,
                "specialist_hourly": specialist_hourly,
                "specialist_daily": specialist_daily,
            },
        )
    finally:
        conn.close()


def _emit_validation_event(csi_db: Path, payload: dict[str, Any]) -> None:
    conn = connect(csi_db)
    ensure_schema(conn)
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        suffix = int(time.time())
        event_id = f"csi:nightly_validation:{suffix}"
        dedupe = f"csi:nightly_validation:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        conn.execute(
            """
            INSERT OR REPLACE INTO events (
                event_id, dedupe_key, source, event_type, occurred_at, received_at, emitted_at,
                subject_json, routing_json, metadata_json, delivered, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                dedupe,
                "csi_analytics",
                "nightly_validation_report",
                now_iso,
                now_iso,
                now_iso,
                json.dumps(payload, ensure_ascii=False),
                json.dumps({"pipeline": "csi_nightly_validation", "priority": "normal"}, ensure_ascii=False),
                json.dumps({"source_adapter": "csi_nightly_validation_v1"}, ensure_ascii=False),
                0,
                now_iso,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
        help="CSI env file used for auth lane resolution.",
    )
    parser.add_argument(
        "--csi-db",
        default="/opt/universal_agent/CSI_Ingester/development/var/csi.db",
        help="Path to CSI sqlite database.",
    )
    parser.add_argument(
        "--runtime-db",
        default="",
        help="Path to UA runtime database. Defaults to UA_RUNTIME_DB_PATH or standard runtime path.",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=24,
        help="Lookback window for validation checks.",
    )
    parser.add_argument(
        "--require-mode1",
        action="store_true",
        help="Fail validation when auth mode is not dedicated lane (mode=1).",
    )
    parser.add_argument(
        "--emit-event",
        action="store_true",
        help="Emit nightly_validation_report event to CSI events table.",
    )
    args = parser.parse_args()

    env_values = _load_env_file(Path(args.env_file).expanduser())
    csi_db = Path(args.csi_db).expanduser()
    runtime_db = Path(args.runtime_db).expanduser() if str(args.runtime_db).strip() else _runtime_db_path_from_env()

    checks = [
        _check_auth_lane(env_values, require_mode1=bool(args.require_mode1)),
        _check_csi_db(csi_db, lookback_hours=max(1, int(args.lookback_hours))),
        _check_runtime_activity(runtime_db, lookback_hours=max(1, int(args.lookback_hours))),
    ]
    ok = all(item.ok for item in checks)
    summary = {
        "status": "ok" if ok else "failed",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": int(args.lookback_hours),
        "checks": [
            {"name": item.name, "ok": item.ok, "detail": item.detail, "metrics": item.metrics}
            for item in checks
        ],
    }

    if args.emit_event and csi_db.exists():
        _emit_validation_event(csi_db, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
