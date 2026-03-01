#!/usr/bin/env python3
"""Run RSS quality gates and emit alert/ok telemetry to UA."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import uuid
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


def _apply_env_defaults(path: Path) -> None:
    for key, val in _load_env_file(path).items():
        os.environ.setdefault(key, val)


def _resolve_setting(keys: list[str], env_file_values: dict[str, str], default: str = "") -> str:
    for key in keys:
        env_val = os.getenv(key, "").strip()
        if env_val:
            return env_val
        file_val = env_file_values.get(key, "").strip()
        if file_val:
            return file_val
    return default


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


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _recent_rss_dlq_count(conn: sqlite3.Connection, window_expr: str) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM dead_letter
            WHERE created_at >= datetime('now', ?)
              AND json_extract(event_json, '$.source') = 'youtube_channel_rss'
            """,
            (window_expr,),
        ).fetchone()
        return int((row["total"] if row is not None else 0) or 0)
    except sqlite3.OperationalError:
        # Fallback when JSON functions are unavailable in sqlite build.
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM dead_letter
            WHERE created_at >= datetime('now', ?)
              AND event_json LIKE '%"source":"youtube_channel_rss"%'
            """,
            (window_expr,),
        ).fetchone()
        return int((row["total"] if row is not None else 0) or 0)


def _get_metrics(conn: sqlite3.Connection, *, window_hours: int) -> dict[str, Any]:
    window_expr = f"-{max(1, int(window_hours))} hours"

    ev_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN delivered = 0 THEN 1 ELSE 0 END) AS undelivered
        FROM events
        WHERE source = 'youtube_channel_rss'
          AND created_at >= datetime('now', ?)
        """,
        (window_expr,),
    ).fetchone()

    analysis_row = conn.execute(
        """
        SELECT
            COUNT(*) AS analyzed_total,
            SUM(CASE WHEN transcript_status = 'ok' THEN 1 ELSE 0 END) AS transcript_ok,
            SUM(CASE WHEN category = 'other_interest' THEN 1 ELSE 0 END) AS other_interest_items
        FROM rss_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
        """,
        (window_expr,),
    ).fetchone()

    stale_row = conn.execute(
        """
        SELECT CAST((julianday('now') - julianday(MAX(created_at))) * 24 * 60 AS INTEGER) AS mins
        FROM events
        WHERE source = 'youtube_channel_rss'
        """
    ).fetchone()

    total = int(ev_row["total"] or 0)
    undelivered = int(ev_row["undelivered"] or 0)
    dlq_recent = _recent_rss_dlq_count(conn, window_expr)

    analyzed_total = int(analysis_row["analyzed_total"] or 0)
    transcript_ok = int(analysis_row["transcript_ok"] or 0)
    other_interest_items = int(analysis_row["other_interest_items"] or 0)

    transcript_ratio = float(transcript_ok / analyzed_total) if analyzed_total > 0 else 0.0
    other_interest_ratio = float(other_interest_items / analyzed_total) if analyzed_total > 0 else 0.0

    latest_rss_age_minutes = int(stale_row["mins"] or 999999)
    if latest_rss_age_minutes < 0:
        latest_rss_age_minutes = 0

    return {
        "window_hours": int(window_hours),
        "rss_events_recent": total,
        "rss_undelivered_recent": undelivered,
        "dlq_recent": dlq_recent,
        "analysis_rows_recent": analyzed_total,
        "transcript_ok_recent": transcript_ok,
        "transcript_ok_ratio": transcript_ratio,
        "other_interest_ratio": other_interest_ratio,
        "latest_rss_age_minutes": latest_rss_age_minutes,
    }


def _check_violations(metrics: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    violations: list[str] = []

    if int(metrics.get("rss_events_recent") or 0) < int(thresholds["min_recent_rss_events"]):
        violations.append(
            f"rss_events_recent_below_min ({int(metrics.get('rss_events_recent') or 0)} < {int(thresholds['min_recent_rss_events'])})"
        )

    if int(metrics.get("rss_undelivered_recent") or 0) > int(thresholds["max_recent_undelivered"]):
        violations.append(
            f"rss_undelivered_recent_exceeds_max ({int(metrics.get('rss_undelivered_recent') or 0)} > {int(thresholds['max_recent_undelivered'])})"
        )

    if int(metrics.get("dlq_recent") or 0) > int(thresholds["max_recent_dlq"]):
        violations.append(f"dlq_recent_exceeds_max ({int(metrics.get('dlq_recent') or 0)} > {int(thresholds['max_recent_dlq'])})")

    if float(metrics.get("transcript_ok_ratio") or 0.0) < float(thresholds["min_transcript_ok_ratio"]):
        violations.append(
            "transcript_ok_ratio_below_min "
            f"({float(metrics.get('transcript_ok_ratio') or 0.0):.3f} < {float(thresholds['min_transcript_ok_ratio']):.3f})"
        )

    if float(metrics.get("other_interest_ratio") or 0.0) > float(thresholds["max_other_interest_ratio"]):
        violations.append(
            "other_interest_ratio_exceeds_max "
            f"({float(metrics.get('other_interest_ratio') or 0.0):.3f} > {float(thresholds['max_other_interest_ratio']):.3f})"
        )

    if int(metrics.get("latest_rss_age_minutes") or 0) > int(thresholds["max_rss_staleness_minutes"]):
        violations.append(
            f"latest_rss_age_exceeds_max ({int(metrics.get('latest_rss_age_minutes') or 0)} > {int(thresholds['max_rss_staleness_minutes'])})"
        )

    return violations


def _resolve_telegram_target(env_file_values: dict[str, str]) -> tuple[str, str]:
    token = _resolve_setting(["CSI_RSS_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"], env_file_values)
    chat_id = _resolve_setting(
        ["CSI_RSS_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID", "TELEGRAM_DEFAULT_CHAT_ID"],
        env_file_values,
    )
    if not chat_id:
        raw_allowed = _resolve_setting(["TELEGRAM_ALLOWED_USER_IDS"], env_file_values)
        if raw_allowed:
            chat_id = raw_allowed.split(",", 1)[0].strip()
    return token, chat_id


def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    try:
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3900]}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"content-type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= int(getattr(resp, "status", 200) or 200) < 300
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RSS quality gates and emit alerts.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--window-hours", type=int, default=6)
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/rss_quality_gate_state.json",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    args = parser.parse_args()

    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())
    env_file_values = _load_env_file(Path(args.env_file).expanduser())
    env_file_values.update(_load_env_file(Path(args.csi_env_file).expanduser()))

    thresholds = {
        "min_recent_rss_events": int(_resolve_setting(["CSI_QUALITY_GATE_MIN_RECENT_RSS_EVENTS"], env_file_values, "1") or "1"),
        "max_recent_undelivered": int(_resolve_setting(["CSI_QUALITY_GATE_MAX_RECENT_UNDELIVERED"], env_file_values, "0") or "0"),
        "max_recent_dlq": int(_resolve_setting(["CSI_QUALITY_GATE_MAX_RECENT_DLQ"], env_file_values, "0") or "0"),
        "min_transcript_ok_ratio": float(_resolve_setting(["CSI_QUALITY_GATE_MIN_TRANSCRIPT_OK_RATIO"], env_file_values, "0.15") or "0.15"),
        "max_other_interest_ratio": float(_resolve_setting(["CSI_QUALITY_GATE_MAX_OTHER_INTEREST_RATIO"], env_file_values, "0.85") or "0.85"),
        "max_rss_staleness_minutes": int(_resolve_setting(["CSI_QUALITY_GATE_MAX_RSS_STALENESS_MINUTES"], env_file_values, "240") or "240"),
    }
    repeat_minutes = int(_resolve_setting(["CSI_QUALITY_GATE_ALERT_REPEAT_MINUTES"], env_file_values, "180") or "180")

    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)
    config = load_config()

    metrics = _get_metrics(conn, window_hours=max(1, int(args.window_hours)))
    violations = _check_violations(metrics, thresholds)
    status = "alert" if violations else "ok"

    state_path = Path(args.state_path).expanduser()
    state = _load_state(state_path)
    last_status = str(state.get("status") or "")
    last_sent_epoch = int(state.get("last_sent_epoch") or 0)
    now_epoch = int(time.time())

    should_emit = bool(args.force) or status != last_status
    if not should_emit and status == "alert":
        should_emit = (now_epoch - last_sent_epoch) >= max(60, repeat_minutes * 60)

    emit_status_code = 0
    emitted = False
    telegram_sent = False

    if should_emit:
        now_iso = _utc_now_iso()
        hour_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        event_type = "rss_quality_gate_alert" if status == "alert" else "rss_quality_gate_ok"
        event = CreatorSignalEvent(
            event_id=f"csi:rss_quality_gate:{status}:{config.instance_id}:{uuid.uuid4().hex[:10]}",
            dedupe_key=f"csi:rss_quality_gate:{config.instance_id}:{status}:{hour_key}",
            source="csi_analytics",
            event_type=event_type,
            occurred_at=now_iso,
            received_at=now_iso,
            subject={
                "report_type": "rss_quality_gate",
                "status": status,
                "window_hours": int(metrics["window_hours"]),
                "metrics": metrics,
                "thresholds": thresholds,
                "violations": violations,
            },
            routing={
                "pipeline": "csi_rss_quality_gate",
                "priority": "urgent" if status == "alert" else "standard",
                "tags": ["csi", "rss", "quality_gate", status],
            },
            metadata={"source_adapter": "csi_rss_quality_gate_v1"},
        )
        emitted, emit_status_code, _payload = emit_and_track(conn, config=config, event=event, retry_count=3)

        if status == "alert":
            token, chat_id = _resolve_telegram_target(env_file_values)
            text = (
                "CSI RSS Quality Alert\n"
                f"Status: {status}\n"
                f"Window: {int(metrics['window_hours'])}h\n"
                f"Violations: {len(violations)}\n"
                + "\n".join(f"- {line}" for line in violations[:8])
            )
            telegram_sent = _send_telegram(token, chat_id, text)

        _save_state(
            state_path,
            {
                "status": status,
                "last_sent_epoch": now_epoch,
                "last_sent_at": _utc_now_iso(),
                "last_emit_status_code": int(emit_status_code),
                "last_emitted": bool(emitted),
                "last_telegram_sent": bool(telegram_sent),
                "violations": violations,
            },
        )

    conn.close()

    print(f"RSS_QUALITY_STATUS={status}")
    print(f"RSS_QUALITY_SHOULD_EMIT={1 if should_emit else 0}")
    print(f"RSS_QUALITY_VIOLATIONS={len(violations)}")
    print(f"RSS_QUALITY_EMITTED={1 if emitted else 0}")
    print(f"RSS_QUALITY_EMIT_STATUS={int(emit_status_code)}")
    print(f"RSS_QUALITY_TELEGRAM_SENT={1 if telegram_sent else 0}")
    if violations:
        for idx, item in enumerate(violations, start=1):
            print(f"RSS_QUALITY_VIOLATION_{idx}={item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
