#!/usr/bin/env python3
"""Daily reliability SLO gatekeeper for CSI delivery health."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import csi_delivery_health_canary as canary
from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store import source_state as source_state_store
from csi_ingester.store.sqlite import connect, ensure_schema

WATCHED_SOURCES: tuple[str, ...] = ("youtube_channel_rss", "reddit_discovery")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sql_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _window_bounds(day: str | None) -> tuple[str, datetime, datetime, datetime, datetime]:
    if day:
        base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        base = today - timedelta(days=1)
    start = base
    end = base + timedelta(days=1)
    previous_start = start - timedelta(days=1)
    previous_end = start
    return base.strftime("%Y-%m-%d"), start, end, previous_start, previous_end


def _delivery_stats(conn, *, start: datetime, end: datetime) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS attempts_total,
            SUM(CASE WHEN delivered = 1 THEN 1 ELSE 0 END) AS attempts_success,
            SUM(CASE WHEN delivered = 0 THEN 1 ELSE 0 END) AS attempts_failed
        FROM delivery_attempts
        WHERE attempted_at >= ? AND attempted_at < ?
        """,
        (_sql_ts(start), _sql_ts(end)),
    ).fetchone()
    attempts_total = int((row["attempts_total"] if row is not None else 0) or 0)
    attempts_success = int((row["attempts_success"] if row is not None else 0) or 0)
    attempts_failed = int((row["attempts_failed"] if row is not None else 0) or 0)
    ratio = float(attempts_success / attempts_total) if attempts_total > 0 else 1.0
    return {
        "attempts_total": attempts_total,
        "attempts_success": attempts_success,
        "attempts_failed": attempts_failed,
        "success_ratio": round(ratio, 4),
    }


def _event_delivery_ratio(conn, *, start: datetime, end: datetime) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS events_total,
            SUM(CASE WHEN delivered = 1 THEN 1 ELSE 0 END) AS events_delivered
        FROM events
        WHERE created_at >= ? AND created_at < ?
        """,
        (_sql_ts(start), _sql_ts(end)),
    ).fetchone()
    total = int((row["events_total"] if row is not None else 0) or 0)
    delivered = int((row["events_delivered"] if row is not None else 0) or 0)
    ratio = float(delivered / total) if total > 0 else 1.0
    return {"events_total": total, "events_delivered": delivered, "event_delivery_ratio": round(ratio, 4)}


def _dlq_backlog(conn, *, start: datetime, end: datetime) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM dead_letter WHERE created_at >= ? AND created_at < ?",
        (_sql_ts(start), _sql_ts(end)),
    ).fetchone()
    return int((row["total"] if row is not None else 0) or 0)


def _source_freshness(conn, *, source_name: str, end: datetime, threshold_minutes: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT MAX(created_at) AS last_event_at
        FROM events
        WHERE source = ? AND created_at < ?
        """,
        (source_name, _sql_ts(end)),
    ).fetchone()
    raw_last = str(row["last_event_at"] or "") if row is not None else ""
    parsed_last = _parse_timestamp(raw_last)
    lag_minutes: float | None = None
    stale = False
    if parsed_last is not None:
        lag_minutes = max(0.0, (end - parsed_last).total_seconds() / 60.0)
        stale = lag_minutes > float(threshold_minutes)
    else:
        stale = True
    return {
        "source": source_name,
        "last_event_at": raw_last or None,
        "lag_minutes": round(float(lag_minutes), 2) if lag_minutes is not None else None,
        "stale": bool(stale),
    }


def _canary_frequency(conn, *, start: datetime, end: datetime) -> dict[str, int]:
    regression_row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM events
        WHERE source = 'csi_analytics'
          AND event_type = 'delivery_health_regression'
          AND COALESCE(occurred_at, created_at) >= ?
          AND COALESCE(occurred_at, created_at) < ?
        """,
        (_sql_ts(start), _sql_ts(end)),
    ).fetchone()
    recovered_row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM events
        WHERE source = 'csi_analytics'
          AND event_type = 'delivery_health_recovered'
          AND COALESCE(occurred_at, created_at) >= ?
          AND COALESCE(occurred_at, created_at) < ?
        """,
        (_sql_ts(start), _sql_ts(end)),
    ).fetchone()
    return {
        "regressions": int((regression_row["total"] if regression_row is not None else 0) or 0),
        "recoveries": int((recovered_row["total"] if recovered_row is not None else 0) or 0),
    }


def _source_runbook(source_name: str) -> str:
    if source_name == "youtube_channel_rss":
        return (
            "systemctl status csi-ingester csi-rss-trend-report.timer csi-rss-telegram-digest.timer && "
            "journalctl -u csi-ingester -n 200 --no-pager"
        )
    if source_name == "reddit_discovery":
        return (
            "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_reddit_probe.py "
            "--watchlist-file /opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json"
        )
    return "journalctl -u csi-ingester -n 200 --no-pager"


def _candidate(
    *,
    code: str,
    title: str,
    severity: str,
    detail: str,
    runbook_command: str,
    metric: dict[str, Any],
    score: float,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "severity": severity,
        "detail": detail,
        "runbook_command": runbook_command,
        "metric": metric,
        "score": round(float(score), 3),
    }


def _evaluate_slo(
    conn,
    *,
    start: datetime,
    end: datetime,
    previous_start: datetime,
    previous_end: datetime,
    min_delivery_success_ratio: float,
    max_dlq_backlog: int,
    max_dlq_backlog_delta: int,
    max_source_lag_minutes: int,
    max_canary_regressions: int,
) -> dict[str, Any]:
    delivery = _delivery_stats(conn, start=start, end=end)
    event_delivery = _event_delivery_ratio(conn, start=start, end=end)
    dlq_current = _dlq_backlog(conn, start=start, end=end)
    dlq_previous = _dlq_backlog(conn, start=previous_start, end=previous_end)
    dlq_delta = dlq_current - dlq_previous
    freshness = [
        _source_freshness(
            conn,
            source_name=source_name,
            end=end,
            threshold_minutes=max_source_lag_minutes,
        )
        for source_name in WATCHED_SOURCES
    ]
    canary_frequency = _canary_frequency(conn, start=start, end=end)

    breaches: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    delivery_ratio = float(delivery["success_ratio"])
    if delivery_ratio < float(min_delivery_success_ratio):
        delta = float(min_delivery_success_ratio) - delivery_ratio
        breaches.append(
            {
                "code": "delivery_success_ratio_below_min",
                "actual": delivery_ratio,
                "threshold": float(min_delivery_success_ratio),
            }
        )
        candidates.append(
            _candidate(
                code="delivery_success_ratio_below_min",
                title="Delivery success ratio below SLO",
                severity="critical",
                detail=(
                    f"Delivery success ratio {delivery_ratio:.4f} is below target "
                    f"{float(min_delivery_success_ratio):.4f}."
                ),
                runbook_command=(
                    "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_replay_dlq.py "
                    "--db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db --limit 200 --max-attempts 3"
                ),
                metric={"actual": delivery_ratio, "target": float(min_delivery_success_ratio)},
                score=95.0 + min(4.0, delta * 100.0),
            )
        )

    if dlq_current > int(max_dlq_backlog):
        breaches.append(
            {
                "code": "dlq_backlog_exceeds_max",
                "actual": int(dlq_current),
                "threshold": int(max_dlq_backlog),
            }
        )
        candidates.append(
            _candidate(
                code="dlq_backlog_exceeds_max",
                title="DLQ backlog exceeds SLO budget",
                severity="critical",
                detail=f"DLQ backlog is {dlq_current} (max allowed {int(max_dlq_backlog)}).",
                runbook_command=(
                    "sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db "
                    "\"select id,event_id,error_reason,created_at from dead_letter order by id desc limit 50;\""
                ),
                metric={"actual": int(dlq_current), "target": int(max_dlq_backlog)},
                score=90.0 + min(8.0, float(dlq_current - int(max_dlq_backlog))),
            )
        )

    if dlq_delta > int(max_dlq_backlog_delta):
        breaches.append(
            {
                "code": "dlq_backlog_trend_up",
                "actual": int(dlq_delta),
                "threshold": int(max_dlq_backlog_delta),
            }
        )
        candidates.append(
            _candidate(
                code="dlq_backlog_trend_up",
                title="DLQ backlog trend is increasing",
                severity="warning",
                detail=(
                    f"DLQ backlog delta is +{dlq_delta} versus previous window "
                    f"(limit +{int(max_dlq_backlog_delta)})."
                ),
                runbook_command=(
                    "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_delivery_health_auto_remediate.py "
                    "--db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db"
                ),
                metric={"actual": int(dlq_delta), "target": int(max_dlq_backlog_delta)},
                score=78.0 + min(10.0, float(dlq_delta - int(max_dlq_backlog_delta))),
            )
        )

    for item in freshness:
        lag = item.get("lag_minutes")
        source_name = str(item.get("source") or "unknown")
        stale = bool(item.get("stale"))
        if not stale:
            continue
        breaches.append(
            {
                "code": "source_freshness_lag_exceeds_max",
                "source": source_name,
                "actual": lag,
                "threshold": int(max_source_lag_minutes),
            }
        )
        lag_score = 0.0
        if isinstance(lag, (int, float)):
            lag_score = min(20.0, float(lag) / max(1.0, float(max_source_lag_minutes)) * 10.0)
        else:
            lag_score = 18.0
        lag_value = "--" if lag is None else f"{float(lag):.1f}m"
        candidates.append(
            _candidate(
                code=f"source_freshness_lag_exceeds_max:{source_name}",
                title=f"{source_name} freshness lag exceeds SLO",
                severity="warning",
                detail=(
                    f"{source_name} freshness lag is {lag_value} "
                    f"(max allowed {int(max_source_lag_minutes)}m)."
                ),
                runbook_command=_source_runbook(source_name),
                metric={
                    "source": source_name,
                    "actual": lag,
                    "target": int(max_source_lag_minutes),
                },
                score=74.0 + lag_score,
            )
        )

    regression_count = int(canary_frequency.get("regressions") or 0)
    if regression_count > int(max_canary_regressions):
        breaches.append(
            {
                "code": "canary_regression_frequency_exceeds_max",
                "actual": regression_count,
                "threshold": int(max_canary_regressions),
            }
        )
        candidates.append(
            _candidate(
                code="canary_regression_frequency_exceeds_max",
                title="Canary regression frequency exceeds budget",
                severity="warning",
                detail=(
                    f"Canary regressions in window: {regression_count} "
                    f"(max allowed {int(max_canary_regressions)})."
                ),
                runbook_command=(
                    "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_delivery_health_canary.py "
                    "--db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db --force"
                ),
                metric={"actual": regression_count, "target": int(max_canary_regressions)},
                score=76.0 + min(12.0, float(regression_count - int(max_canary_regressions))),
            )
        )

    candidates.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    top_candidates = candidates[:3]
    status = "breached" if breaches else "ok"
    return {
        "status": status,
        "window_start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_end_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": {
            "delivery_attempts_total": int(delivery["attempts_total"]),
            "delivery_attempts_success": int(delivery["attempts_success"]),
            "delivery_attempts_failed": int(delivery["attempts_failed"]),
            "delivery_success_ratio": float(delivery["success_ratio"]),
            "events_total": int(event_delivery["events_total"]),
            "events_delivered": int(event_delivery["events_delivered"]),
            "event_delivery_ratio": float(event_delivery["event_delivery_ratio"]),
            "dlq_backlog_current": int(dlq_current),
            "dlq_backlog_previous": int(dlq_previous),
            "dlq_backlog_delta": int(dlq_delta),
            "source_freshness": freshness,
            "canary_regression_count": regression_count,
            "canary_recovery_count": int(canary_frequency.get("recoveries") or 0),
        },
        "thresholds": {
            "min_delivery_success_ratio": float(min_delivery_success_ratio),
            "max_dlq_backlog": int(max_dlq_backlog),
            "max_dlq_backlog_delta": int(max_dlq_backlog_delta),
            "max_source_lag_minutes": int(max_source_lag_minutes),
            "max_canary_regressions": int(max_canary_regressions),
        },
        "breaches": breaches,
        "root_cause_candidates": candidates,
        "top_root_causes": top_candidates,
    }


def _slo_transition(previous_status: str, current_status: str, *, force: bool) -> dict[str, Any]:
    prev = str(previous_status or "").strip().lower() or "unknown"
    curr = str(current_status or "").strip().lower() or "unknown"
    if curr == "breached":
        if force:
            return {"emit": True, "event_type": "delivery_reliability_slo_breached", "reason": "forced"}
        if prev != "breached":
            return {"emit": True, "event_type": "delivery_reliability_slo_breached", "reason": "opened"}
        return {"emit": True, "event_type": "delivery_reliability_slo_breached", "reason": "daily_breach"}
    if prev == "breached" and curr == "ok":
        return {"emit": True, "event_type": "delivery_reliability_slo_recovered", "reason": "recovered"}
    return {"emit": False, "event_type": "", "reason": "no_change"}


def _build_slo_event(
    *,
    config_instance_id: str,
    event_type: str,
    reason: str,
    evaluation: dict[str, Any],
    now_iso: str,
    target_day: str,
) -> CreatorSignalEvent:
    suffix = uuid.uuid4().hex[:10]
    priority = "urgent" if event_type.endswith("_breached") else "standard"
    top_root_causes = evaluation.get("top_root_causes")
    if not isinstance(top_root_causes, list):
        top_root_causes = []
    return CreatorSignalEvent(
        event_id=f"csi:delivery_slo:{event_type}:{config_instance_id}:{suffix}",
        dedupe_key=f"csi:delivery_slo:{event_type}:{config_instance_id}:{target_day}",
        source="csi_analytics",
        event_type=event_type,
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "report_type": "delivery_reliability_slo",
            "status": str(evaluation.get("status") or "unknown"),
            "target_day_utc": target_day,
            "window_start_utc": str(evaluation.get("window_start_utc") or ""),
            "window_end_utc": str(evaluation.get("window_end_utc") or ""),
            "transition_reason": reason,
            "metrics": evaluation.get("metrics") if isinstance(evaluation.get("metrics"), dict) else {},
            "thresholds": evaluation.get("thresholds") if isinstance(evaluation.get("thresholds"), dict) else {},
            "breaches": evaluation.get("breaches") if isinstance(evaluation.get("breaches"), list) else [],
            "top_root_causes": top_root_causes,
            "root_cause_candidates": (
                evaluation.get("root_cause_candidates")
                if isinstance(evaluation.get("root_cause_candidates"), list)
                else []
            ),
        },
        routing={
            "pipeline": "csi_delivery_reliability_slo",
            "priority": priority,
            "tags": ["csi", "delivery_health", "slo", str(evaluation.get("status") or ""), event_type],
        },
        metadata={"source_adapter": "csi_delivery_slo_gatekeeper_v1"},
    )


def _append_history(
    previous_state: dict[str, Any],
    *,
    target_day: str,
    status: str,
    top_root_causes: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    history = previous_state.get("history")
    rows = history if isinstance(history, list) else []
    cleaned = [item for item in rows if isinstance(item, dict)]
    cleaned.append(
        {
            "target_day_utc": target_day,
            "status": str(status or ""),
            "delivery_success_ratio": float(metrics.get("delivery_success_ratio") or 0.0),
            "dlq_backlog_current": int(metrics.get("dlq_backlog_current") or 0),
            "canary_regression_count": int(metrics.get("canary_regression_count") or 0),
            "top_root_cause_codes": [
                str(item.get("code") or "")
                for item in top_root_causes
                if isinstance(item, dict) and item.get("code")
            ],
        }
    )
    return cleaned[-60:]


def _run_once(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    canary._apply_env_defaults(Path(args.csi_env_file).expanduser())
    canary._apply_env_defaults(Path(args.env_file).expanduser())
    env_values = canary._load_env_file(Path(args.env_file).expanduser())
    env_values.update(canary._load_env_file(Path(args.csi_env_file).expanduser()))

    target_day, start_dt, end_dt, previous_start_dt, previous_end_dt = _window_bounds(str(args.day or "").strip() or None)
    tuned = {
        "min_delivery_success_ratio": max(
            0.0,
            min(
                1.0,
                float(
                    canary._resolve_setting(
                        ["UA_CSI_SLO_MIN_DELIVERY_SUCCESS_RATIO"],
                        env_values,
                        str(args.min_delivery_success_ratio),
                    )
                ),
            ),
        ),
        "max_dlq_backlog": max(
            0,
            int(
                canary._resolve_setting(
                    ["UA_CSI_SLO_MAX_DLQ_BACKLOG"],
                    env_values,
                    str(args.max_dlq_backlog),
                )
            ),
        ),
        "max_dlq_backlog_delta": max(
            0,
            int(
                canary._resolve_setting(
                    ["UA_CSI_SLO_MAX_DLQ_BACKLOG_DELTA"],
                    env_values,
                    str(args.max_dlq_backlog_delta),
                )
            ),
        ),
        "max_source_lag_minutes": max(
            30,
            int(
                canary._resolve_setting(
                    ["UA_CSI_SLO_MAX_SOURCE_LAG_MINUTES"],
                    env_values,
                    str(args.max_source_lag_minutes),
                )
            ),
        ),
        "max_canary_regressions": max(
            0,
            int(
                canary._resolve_setting(
                    ["UA_CSI_SLO_MAX_CANARY_REGRESSIONS_24H"],
                    env_values,
                    str(args.max_canary_regressions),
                )
            ),
        ),
    }

    db_path = Path(args.db_path).expanduser()
    conn = connect(db_path)
    ensure_schema(conn)
    config = load_config()
    now_iso = _utc_now_iso()

    evaluation = _evaluate_slo(
        conn,
        start=start_dt,
        end=end_dt,
        previous_start=previous_start_dt,
        previous_end=previous_end_dt,
        min_delivery_success_ratio=float(tuned["min_delivery_success_ratio"]),
        max_dlq_backlog=int(tuned["max_dlq_backlog"]),
        max_dlq_backlog_delta=int(tuned["max_dlq_backlog_delta"]),
        max_source_lag_minutes=int(tuned["max_source_lag_minutes"]),
        max_canary_regressions=int(tuned["max_canary_regressions"]),
    )

    previous_state = source_state_store.get_state(conn, str(args.state_key)) or {}
    transition = _slo_transition(
        str(previous_state.get("status") or "unknown"),
        str(evaluation.get("status") or "unknown"),
        force=bool(args.force),
    )

    emitted = False
    emit_status_code = 0
    emit_payload: dict[str, Any] = {}
    if bool(transition["emit"]) and not bool(args.dry_run):
        event = _build_slo_event(
            config_instance_id=str(config.instance_id),
            event_type=str(transition["event_type"]),
            reason=str(transition["reason"]),
            evaluation=evaluation,
            now_iso=now_iso,
            target_day=target_day,
        )
        emitted, emit_status_code, emit_payload = emit_and_track(conn, config=config, event=event, retry_count=3)

    metrics = evaluation.get("metrics") if isinstance(evaluation.get("metrics"), dict) else {}
    top_root_causes = evaluation.get("top_root_causes") if isinstance(evaluation.get("top_root_causes"), list) else []
    next_state = {
        "status": str(evaluation.get("status") or "unknown"),
        "target_day_utc": target_day,
        "last_checked_at": now_iso,
        "window_start_utc": str(evaluation.get("window_start_utc") or ""),
        "window_end_utc": str(evaluation.get("window_end_utc") or ""),
        "thresholds": tuned,
        "metrics": metrics,
        "breaches": evaluation.get("breaches") if isinstance(evaluation.get("breaches"), list) else [],
        "root_cause_candidates": (
            evaluation.get("root_cause_candidates")
            if isinstance(evaluation.get("root_cause_candidates"), list)
            else []
        ),
        "top_root_causes": top_root_causes,
        "last_transition_reason": str(transition.get("reason") or ""),
        "last_event_type": str(transition.get("event_type") or ""),
        "last_emit_status_code": int(emit_status_code or 0),
        "last_emitted_ok": bool(emitted),
        "history": _append_history(
            previous_state,
            target_day=target_day,
            status=str(evaluation.get("status") or "unknown"),
            top_root_causes=[item for item in top_root_causes if isinstance(item, dict)],
            metrics=metrics,
        ),
    }
    source_state_store.set_state(conn, str(args.state_key), next_state)
    conn.close()

    summary = {
        "target_day_utc": target_day,
        "status": str(evaluation.get("status") or "unknown"),
        "window_start_utc": str(evaluation.get("window_start_utc") or ""),
        "window_end_utc": str(evaluation.get("window_end_utc") or ""),
        "transition": transition,
        "tuning": tuned,
        "top_root_causes": top_root_causes,
        "breach_count": len(evaluation.get("breaches") or []),
        "emitted": bool(emitted),
        "emit_status_code": int(emit_status_code or 0),
        "emit_payload": emit_payload if isinstance(emit_payload, dict) else {"payload": emit_payload},
        "dry_run": bool(args.dry_run),
    }
    if bool(transition["emit"]) and not bool(args.dry_run) and not bool(emitted):
        return 1, summary
    return 0, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute daily CSI delivery reliability SLO status and emit alerts.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--state-key", default="runtime_canary:delivery_slo")
    parser.add_argument("--day", default="", help="UTC day YYYY-MM-DD. Defaults to yesterday.")
    parser.add_argument("--min-delivery-success-ratio", type=float, default=0.98)
    parser.add_argument("--max-dlq-backlog", type=int, default=0)
    parser.add_argument("--max-dlq-backlog-delta", type=int, default=0)
    parser.add_argument("--max-source-lag-minutes", type=int, default=240)
    parser.add_argument("--max-canary-regressions", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    args = parser.parse_args()
    code, summary = _run_once(args)
    print("CSI_DELIVERY_SLO_GATEKEEPER", json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    if code != 0:
        print("CSI_DELIVERY_SLO_GATEKEEPER_EMIT_FAILED")
        return code
    print("CSI_DELIVERY_SLO_GATEKEEPER_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
