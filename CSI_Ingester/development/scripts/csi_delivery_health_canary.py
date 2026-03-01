#!/usr/bin/env python3
"""Runtime canary for CSI delivery-health regressions with guided remediation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
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
from csi_ingester.store import source_state as source_state_store
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


def _parse_timestamp(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _recent_dlq_count(conn: sqlite3.Connection, *, source_name: str, window_expr: str) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM dead_letter
            WHERE created_at >= datetime('now', ?)
              AND json_extract(event_json, '$.source') = ?
            """,
            (window_expr, source_name),
        ).fetchone()
        return int((row["total"] if row is not None else 0) or 0)
    except sqlite3.OperationalError:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM dead_letter
            WHERE created_at >= datetime('now', ?)
              AND event_json LIKE ?
            """,
            (window_expr, f'%"source":"{source_name}"%'),
        ).fetchone()
        return int((row["total"] if row is not None else 0) or 0)


def _source_metrics(
    conn: sqlite3.Connection,
    *,
    source_name: str,
    window_hours: int,
    stale_threshold_minutes: int,
    max_failed_attempt_ratio: float,
    expected_min_events: int,
    max_dlq_recent: int,
    adapter_failures_threshold: int,
) -> dict[str, Any]:
    window_expr = f"-{max(1, int(window_hours))} hours"
    event_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN delivered = 1 THEN 1 ELSE 0 END) AS delivered_total,
            SUM(CASE WHEN delivered = 0 THEN 1 ELSE 0 END) AS undelivered_total,
            MAX(created_at) AS last_event_at
        FROM events
        WHERE source = ?
          AND created_at >= datetime('now', ?)
        """,
        (source_name, window_expr),
    ).fetchone()

    last_any_row = conn.execute(
        """
        SELECT MAX(created_at) AS last_event_at
        FROM events
        WHERE source = ?
        """,
        (source_name,),
    ).fetchone()

    attempts_row = conn.execute(
        """
        SELECT
            COUNT(*) AS attempts,
            SUM(CASE WHEN da.delivered = 1 THEN 1 ELSE 0 END) AS attempts_delivered,
            SUM(CASE WHEN da.delivered = 0 THEN 1 ELSE 0 END) AS attempts_failed
        FROM delivery_attempts da
        JOIN events e ON e.event_id = da.event_id
        WHERE e.source = ?
          AND da.attempted_at >= datetime('now', ?)
        """,
        (source_name, window_expr),
    ).fetchone()

    total = int((event_row["total"] if event_row is not None else 0) or 0)
    delivered_total = int((event_row["delivered_total"] if event_row is not None else 0) or 0)
    undelivered_total = int((event_row["undelivered_total"] if event_row is not None else 0) or 0)
    attempts = int((attempts_row["attempts"] if attempts_row is not None else 0) or 0)
    attempts_delivered = int((attempts_row["attempts_delivered"] if attempts_row is not None else 0) or 0)
    attempts_failed = int((attempts_row["attempts_failed"] if attempts_row is not None else 0) or 0)
    failed_attempt_ratio = float(attempts_failed / attempts) if attempts > 0 else 0.0
    dlq_recent = _recent_dlq_count(conn, source_name=source_name, window_expr=window_expr)

    adapter_state = source_state_store.get_state(conn, f"adapter_health:{source_name}") or {}
    adapter_failures = int(adapter_state.get("consecutive_failures") or 0)
    adapter_last_error = str(adapter_state.get("last_error") or "").strip()

    now_ts = time.time()
    last_event_at = str(event_row["last_event_at"] or "") if event_row is not None else ""
    last_any_event_at = str(last_any_row["last_event_at"] or "") if last_any_row is not None else ""
    parsed_last_any = _parse_timestamp(last_any_event_at)
    lag_minutes: float | None = None
    if parsed_last_any is not None:
        lag_minutes = max(0.0, (now_ts - parsed_last_any.timestamp()) / 60.0)

    under_min_volume = int(expected_min_events) > 0 and total < int(expected_min_events)
    stale = bool(int(expected_min_events) > 0 and (total == 0 or (lag_minutes is not None and lag_minutes > float(stale_threshold_minutes))))
    high_failed_ratio = attempts > 0 and failed_attempt_ratio > float(max_failed_attempt_ratio)
    all_failed = attempts > 0 and attempts_failed == attempts
    dlq_exceeds = dlq_recent > int(max_dlq_recent)
    adapter_unhealthy = adapter_failures >= int(adapter_failures_threshold)

    status = "ok"
    if under_min_volume or stale:
        status = "degraded"
    if attempts_failed > 0 or dlq_exceeds:
        status = "degraded"
    if high_failed_ratio or all_failed or adapter_unhealthy:
        status = "failing"

    repair_hints: list[dict[str, Any]] = []
    if source_name == "youtube_channel_rss" and (under_min_volume or stale):
        repair_hints.append(
            {
                "code": "rss_source_stale_or_low_volume",
                "source": source_name,
                "severity": "warning",
                "title": "RSS source volume below threshold",
                "action": "Check RSS watchlist resolution and timer health.",
                "runbook_command": (
                    "systemctl status csi-ingester csi-rss-trend-report.timer csi-rss-telegram-digest.timer && "
                    "journalctl -u csi-ingester -n 120 --no-pager"
                ),
            }
        )
    if source_name == "reddit_discovery" and (under_min_volume or stale):
        repair_hints.append(
            {
                "code": "reddit_source_stale_or_low_volume",
                "source": source_name,
                "severity": "warning",
                "title": "Reddit source volume below threshold",
                "action": "Verify subreddit watchlist and endpoint fallback behavior.",
                "runbook_command": (
                    "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_reddit_probe.py "
                    "--watchlist-file /opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json"
                ),
            }
        )
    if attempts_failed > 0 or high_failed_ratio:
        repair_hints.append(
            {
                "code": "delivery_failures_detected",
                "source": source_name,
                "severity": "critical" if status == "failing" else "warning",
                "title": "CSI->UA delivery failures detected",
                "action": "Verify ingest auth/endpoint and replay DLQ after repair.",
                "runbook_command": (
                    "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_replay_dlq.py "
                    "--db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db --limit 100 --max-attempts 3"
                ),
            }
        )
    if dlq_exceeds:
        repair_hints.append(
            {
                "code": "dlq_backlog_exceeds_threshold",
                "source": source_name,
                "severity": "critical",
                "title": "DLQ backlog exceeds threshold",
                "action": "Inspect newest DLQ errors and replay once root cause is fixed.",
                "runbook_command": (
                    "sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db "
                    "\"select id,event_id,error_reason,created_at from dead_letter order by id desc limit 25;\""
                ),
            }
        )
    if adapter_unhealthy:
        repair_hints.append(
            {
                "code": "adapter_consecutive_failures",
                "source": source_name,
                "severity": "critical",
                "title": f"Adapter failing repeatedly ({adapter_failures} consecutive failures)",
                "action": "Inspect adapter logs and recover source connectivity/parsing.",
                "runbook_command": "journalctl -u csi-ingester -n 200 --no-pager",
                "detail": adapter_last_error,
            }
        )

    return {
        "source": source_name,
        "status": status,
        "events_recent": total,
        "delivered_recent": delivered_total,
        "undelivered_recent": undelivered_total,
        "delivery_attempts_recent": attempts,
        "delivery_attempts_success": attempts_delivered,
        "delivery_attempts_failed": attempts_failed,
        "failed_attempt_ratio": round(failed_attempt_ratio, 4),
        "dlq_recent": dlq_recent,
        "expected_min_events": int(expected_min_events),
        "last_event_at": last_event_at,
        "last_any_event_at": last_any_event_at,
        "lag_minutes": round(float(lag_minutes), 2) if lag_minutes is not None else None,
        "adapter_health": adapter_state,
        "adapter_consecutive_failures": adapter_failures,
        "repair_hints": repair_hints,
    }


def _overall_status(rows: list[dict[str, Any]]) -> str:
    has_failing = any(str(item.get("status") or "") == "failing" for item in rows)
    has_degraded = any(str(item.get("status") or "") == "degraded" for item in rows)
    if has_failing:
        return "failing"
    if has_degraded:
        return "degraded"
    return "ok"


def _build_remediation_steps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in rows:
        hints = row.get("repair_hints")
        if not isinstance(hints, list):
            continue
        for hint in hints:
            if not isinstance(hint, dict):
                continue
            code = str(hint.get("code") or "").strip()
            source = str(hint.get("source") or row.get("source") or "").strip()
            command = str(hint.get("runbook_command") or "").strip()
            dedupe_key = f"{code}:{source}:{command}"
            if not code or not command or dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            out.append(
                {
                    "code": code,
                    "source": source,
                    "title": str(hint.get("title") or "Remediation step"),
                    "severity": str(hint.get("severity") or "warning"),
                    "action": str(hint.get("action") or ""),
                    "runbook_command": command,
                    "detail": str(hint.get("detail") or ""),
                }
            )
    severity_rank = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    out.sort(key=lambda item: (severity_rank.get(str(item.get("severity") or "warning"), 9), str(item.get("source") or ""), str(item.get("code") or "")))
    return out


def _evaluate_delivery_health(
    conn: sqlite3.Connection,
    *,
    window_hours: int,
    stale_minutes: int,
    max_failed_attempt_ratio: float,
    min_rss_events: int,
    min_reddit_events: int,
    max_dlq_recent: int,
    adapter_failures_threshold: int,
) -> dict[str, Any]:
    sources = [
        ("youtube_channel_rss", int(min_rss_events)),
        ("reddit_discovery", int(min_reddit_events)),
        ("csi_analytics", 0),
    ]
    rows = [
        _source_metrics(
            conn,
            source_name=name,
            window_hours=window_hours,
            stale_threshold_minutes=stale_minutes,
            max_failed_attempt_ratio=max_failed_attempt_ratio,
            expected_min_events=min_events,
            max_dlq_recent=max_dlq_recent,
            adapter_failures_threshold=adapter_failures_threshold,
        )
        for name, min_events in sources
    ]
    overall = _overall_status(rows)
    remediation_steps = _build_remediation_steps(rows)
    failing_sources = [str(item.get("source") or "") for item in rows if str(item.get("status") or "") == "failing"]
    degraded_sources = [str(item.get("source") or "") for item in rows if str(item.get("status") or "") == "degraded"]
    return {
        "status": overall,
        "sources": rows,
        "failing_sources": failing_sources,
        "degraded_sources": degraded_sources,
        "remediation_steps": remediation_steps,
    }


def _canary_transition(
    *,
    previous_status: str,
    current_status: str,
    previous_alert_epoch: int,
    now_epoch: int,
    repeat_minutes: int,
    force: bool,
) -> dict[str, Any]:
    prev = str(previous_status or "").strip().lower() or "unknown"
    curr = str(current_status or "").strip().lower() or "unknown"
    is_regression = curr in {"degraded", "failing"}
    was_regression = prev in {"degraded", "failing"}
    repeat_seconds = max(60, int(repeat_minutes) * 60)

    if force and is_regression:
        return {"emit": True, "event_type": "delivery_health_regression", "reason": "forced"}
    if not was_regression and is_regression:
        return {"emit": True, "event_type": "delivery_health_regression", "reason": "opened"}
    if was_regression and not is_regression and curr == "ok":
        return {"emit": True, "event_type": "delivery_health_recovered", "reason": "recovered"}
    if is_regression and was_regression and (now_epoch - int(previous_alert_epoch or 0)) >= repeat_seconds:
        return {"emit": True, "event_type": "delivery_health_regression", "reason": "reminder"}
    return {"emit": False, "event_type": "", "reason": "no_change"}


def _build_canary_event(
    *,
    config_instance_id: str,
    event_type: str,
    reason: str,
    now_iso: str,
    health: dict[str, Any],
    tuning: dict[str, Any],
) -> CreatorSignalEvent:
    suffix = uuid.uuid4().hex[:10]
    hour_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    summary = (
        f"Delivery health {health['status']} "
        f"(failing={len(health['failing_sources'])}, degraded={len(health['degraded_sources'])})"
    )
    subject = {
        "report_type": "delivery_health_canary",
        "status": str(health["status"]),
        "transition_reason": str(reason),
        "summary": summary,
        "failing_sources": list(health["failing_sources"]),
        "degraded_sources": list(health["degraded_sources"]),
        "sources": list(health["sources"]),
        "tuning": tuning,
        "remediation": {
            "steps": list(health["remediation_steps"]),
            "next_step": (health["remediation_steps"][0] if health["remediation_steps"] else None),
        },
    }
    priority = "urgent" if str(health["status"]) == "failing" else "high"
    if event_type == "delivery_health_recovered":
        priority = "standard"
    return CreatorSignalEvent(
        event_id=f"csi:delivery_health_canary:{event_type}:{config_instance_id}:{suffix}",
        dedupe_key=f"csi:delivery_health_canary:{event_type}:{config_instance_id}:{hour_key}:{reason}",
        source="csi_analytics",
        event_type=event_type,
        occurred_at=now_iso,
        received_at=now_iso,
        subject=subject,
        routing={
            "pipeline": "csi_delivery_health_canary",
            "priority": priority,
            "tags": ["csi", "delivery_health", "canary", str(health["status"]), event_type],
        },
        metadata={"source_adapter": "csi_delivery_health_canary_v1"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit CSI delivery-health regression/recovery canary events.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--state-key", default="runtime_canary:delivery_health")
    parser.add_argument("--window-hours", type=int, default=6)
    parser.add_argument("--repeat-minutes", type=int, default=45)
    parser.add_argument("--stale-minutes", type=int, default=240)
    parser.add_argument("--max-failed-attempt-ratio", type=float, default=0.20)
    parser.add_argument("--min-rss-events", type=int, default=1)
    parser.add_argument("--min-reddit-events", type=int, default=1)
    parser.add_argument("--max-dlq-recent", type=int, default=0)
    parser.add_argument("--adapter-consecutive-failures", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
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

    tuned = {
        "window_hours": max(
            1,
            int(
                _resolve_setting(
                    ["UA_CSI_DELIVERY_CANARY_WINDOW_HOURS"],
                    env_file_values,
                    str(args.window_hours),
                )
            ),
        ),
        "repeat_minutes": max(
            1,
            int(
                _resolve_setting(
                    ["UA_CSI_DELIVERY_CANARY_REPEAT_MINUTES"],
                    env_file_values,
                    str(args.repeat_minutes),
                )
            ),
        ),
        "stale_minutes": max(
            30,
            int(
                _resolve_setting(
                    ["UA_CSI_DELIVERY_STALE_MINUTES"],
                    env_file_values,
                    str(args.stale_minutes),
                )
            ),
        ),
        "max_failed_attempt_ratio": max(
            0.0,
            min(
                1.0,
                float(
                    _resolve_setting(
                        ["UA_CSI_DELIVERY_MAX_FAILED_ATTEMPT_RATIO"],
                        env_file_values,
                        str(args.max_failed_attempt_ratio),
                    )
                ),
            ),
        ),
        "min_rss_events": max(
            0,
            int(
                _resolve_setting(
                    ["UA_CSI_DELIVERY_MIN_RSS_EVENTS_24H"],
                    env_file_values,
                    str(args.min_rss_events),
                )
            ),
        ),
        "min_reddit_events": max(
            0,
            int(
                _resolve_setting(
                    ["UA_CSI_DELIVERY_MIN_REDDIT_EVENTS_24H"],
                    env_file_values,
                    str(args.min_reddit_events),
                )
            ),
        ),
        "max_dlq_recent": max(
            0,
            int(
                _resolve_setting(
                    ["UA_CSI_DELIVERY_MAX_DLQ_RECENT"],
                    env_file_values,
                    str(args.max_dlq_recent),
                )
            ),
        ),
        "adapter_consecutive_failures": max(
            1,
            int(
                _resolve_setting(
                    ["UA_CSI_DELIVERY_ADAPTER_CONSECUTIVE_FAILURES"],
                    env_file_values,
                    str(args.adapter_consecutive_failures),
                )
            ),
        ),
    }

    db_path = Path(args.db_path).expanduser()
    conn = connect(db_path)
    ensure_schema(conn)
    config = load_config()
    now_iso = _utc_now_iso()
    now_epoch = int(time.time())

    health = _evaluate_delivery_health(
        conn,
        window_hours=int(tuned["window_hours"]),
        stale_minutes=int(tuned["stale_minutes"]),
        max_failed_attempt_ratio=float(tuned["max_failed_attempt_ratio"]),
        min_rss_events=int(tuned["min_rss_events"]),
        min_reddit_events=int(tuned["min_reddit_events"]),
        max_dlq_recent=int(tuned["max_dlq_recent"]),
        adapter_failures_threshold=int(tuned["adapter_consecutive_failures"]),
    )
    previous_state = source_state_store.get_state(conn, str(args.state_key)) or {}
    transition = _canary_transition(
        previous_status=str(previous_state.get("status") or "unknown"),
        current_status=str(health["status"]),
        previous_alert_epoch=int(previous_state.get("last_alert_epoch") or 0),
        now_epoch=now_epoch,
        repeat_minutes=int(tuned["repeat_minutes"]),
        force=bool(args.force),
    )

    emitted = False
    emit_status_code = 0
    emit_payload: dict[str, Any] = {}
    if bool(transition["emit"]) and not bool(args.dry_run):
        event = _build_canary_event(
            config_instance_id=str(config.instance_id),
            event_type=str(transition["event_type"]),
            reason=str(transition["reason"]),
            now_iso=now_iso,
            health=health,
            tuning=tuned,
        )
        emitted, emit_status_code, emit_payload = emit_and_track(conn, config=config, event=event, retry_count=3)

    next_state = {
        "status": str(health["status"]),
        "last_checked_at": now_iso,
        "last_alert_epoch": int(
            now_epoch
            if bool(transition["emit"]) and str(transition["event_type"]) == "delivery_health_regression"
            else int(previous_state.get("last_alert_epoch") or 0)
        ),
        "last_recovered_epoch": int(
            now_epoch
            if bool(transition["emit"]) and str(transition["event_type"]) == "delivery_health_recovered"
            else int(previous_state.get("last_recovered_epoch") or 0)
        ),
        "last_transition_reason": str(transition["reason"]),
        "last_event_type": str(transition["event_type"] or ""),
        "last_emit_status_code": int(emit_status_code or 0),
        "last_emitted_ok": bool(emitted),
        "failing_sources": list(health["failing_sources"]),
        "degraded_sources": list(health["degraded_sources"]),
    }
    source_state_store.set_state(conn, str(args.state_key), next_state)
    conn.close()

    summary = {
        "checked_at": now_iso,
        "status": str(health["status"]),
        "failing_sources": list(health["failing_sources"]),
        "degraded_sources": list(health["degraded_sources"]),
        "remediation_steps": list(health["remediation_steps"]),
        "transition": transition,
        "emitted": bool(emitted),
        "emit_status_code": int(emit_status_code or 0),
        "emit_payload": emit_payload if isinstance(emit_payload, dict) else {"payload": emit_payload},
        "tuning": tuned,
        "dry_run": bool(args.dry_run),
    }
    print("CSI_DELIVERY_CANARY", json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    if bool(transition["emit"]) and not bool(args.dry_run) and not bool(emitted):
        print("CSI_DELIVERY_CANARY_EMIT_FAILED")
        return 1
    print("CSI_DELIVERY_CANARY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

