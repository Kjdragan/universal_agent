#!/usr/bin/env python3
"""Guarded auto-remediation runner for CSI delivery-health regressions."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import csi_delivery_health_canary as canary
from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import CSIConfig, load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.store import dlq as dlq_store
from csi_ingester.store import events as event_store
from csi_ingester.store import source_state as source_state_store
from csi_ingester.store.sqlite import connect, ensure_schema


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_attempts(state: dict[str, Any], action_key: str) -> list[int]:
    actions = state.get("actions") if isinstance(state.get("actions"), dict) else {}
    row = actions.get(action_key) if isinstance(actions, dict) else {}
    attempts = row.get("attempt_epochs") if isinstance(row, dict) else []
    if not isinstance(attempts, list):
        return []
    out: list[int] = []
    for item in attempts:
        try:
            out.append(int(item))
        except Exception:
            continue
    return out


def _guardrail_decision(
    *,
    state: dict[str, Any],
    action_key: str,
    now_epoch: int,
    cooldown_minutes: int,
    max_attempts_per_window: int,
    attempt_window_minutes: int,
) -> tuple[bool, str]:
    actions = state.get("actions") if isinstance(state.get("actions"), dict) else {}
    row = actions.get(action_key) if isinstance(actions, dict) else {}
    last_attempt_epoch = int(row.get("last_attempt_epoch") or 0) if isinstance(row, dict) else 0
    cooldown_seconds = max(60, int(cooldown_minutes) * 60)
    if last_attempt_epoch > 0 and (now_epoch - last_attempt_epoch) < cooldown_seconds:
        return False, "cooldown_active"

    window_seconds = max(600, int(attempt_window_minutes) * 60)
    attempts = [ts for ts in _state_attempts(state, action_key) if (now_epoch - ts) <= window_seconds]
    if len(attempts) >= max(1, int(max_attempts_per_window)):
        return False, "max_attempts_reached"
    return True, "ok"


def _record_action_attempt(
    *,
    state: dict[str, Any],
    action_key: str,
    now_epoch: int,
    success: bool,
    detail: str,
    attempt_window_minutes: int,
) -> None:
    actions = state.setdefault("actions", {})
    if not isinstance(actions, dict):
        actions = {}
        state["actions"] = actions
    row = actions.setdefault(action_key, {})
    if not isinstance(row, dict):
        row = {}
        actions[action_key] = row
    attempts = _state_attempts(state, action_key)
    attempts.append(int(now_epoch))
    window_seconds = max(600, int(attempt_window_minutes) * 60)
    attempts = [ts for ts in attempts if (now_epoch - ts) <= window_seconds]
    row["attempt_epochs"] = attempts
    row["last_attempt_epoch"] = int(now_epoch)
    row["last_success"] = bool(success)
    row["last_detail"] = str(detail or "")[:800]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _max_event_id_for_source(conn: sqlite3.Connection, source_name: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM events WHERE source = ?",
        (source_name,),
    ).fetchone()
    return int((row["max_id"] if row is not None else 0) or 0)


def _cursor_reset_if_stale(*, conn: sqlite3.Connection, state_path: Path, source_name: str, dry_run: bool) -> dict[str, Any]:
    state = _load_json(state_path)
    last_sent_id = int(state.get("last_sent_id") or 0)
    max_id = _max_event_id_for_source(conn, source_name)
    if last_sent_id <= max_id:
        return {
            "ok": True,
            "changed": False,
            "detail": f"cursor_ok last_sent_id={last_sent_id} max_id={max_id}",
            "runbook_command": f"python3 {state_path}",
        }
    if not dry_run:
        next_state = dict(state)
        next_state["last_sent_id"] = 0
        _save_json(state_path, next_state)
    return {
        "ok": True,
        "changed": True,
        "detail": (
            f"cursor_reset source={source_name} state_path={state_path} "
            f"last_sent_id={last_sent_id} max_id={max_id} dry_run={int(dry_run)}"
        ),
        "runbook_command": (
            "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_rss_telegram_digest.py "
            "--db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db"
            if source_name == "youtube_channel_rss"
            else "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_reddit_telegram_digest.py "
            "--db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db"
        ),
    }


async def _replay_dlq(*, conn: sqlite3.Connection, config: CSIConfig, limit: int, max_attempts: int, dry_run: bool) -> dict[str, Any]:
    rows = dlq_store.list_entries(conn, event_id="", limit=max(1, int(limit)))
    if not rows:
        return {"ok": True, "changed": False, "detail": "dlq_empty", "replayed": 0, "failed": 0}
    if dry_run:
        return {
            "ok": True,
            "changed": True,
            "detail": f"dlq_replay_dry_run candidates={len(rows)}",
            "replayed": len(rows),
            "failed": 0,
        }
    if not config.ua_endpoint or not config.ua_shared_secret:
        return {
            "ok": False,
            "changed": False,
            "detail": "ua_delivery_not_configured",
            "replayed": 0,
            "failed": len(rows),
        }
    emitter = UAEmitter(endpoint=config.ua_endpoint, shared_secret=config.ua_shared_secret, instance_id=config.instance_id)
    replayed = 0
    failed = 0
    for row in rows:
        row_id = int(row["id"])
        event_id = str(row["event_id"] or "")
        try:
            payload = json.loads(str(row["event_json"] or "{}"))
            event = CreatorSignalEvent.model_validate(payload)
        except Exception:
            failed += 1
            continue
        delivered, _status_code, _body = await emitter.emit_with_retries([event], max_attempts=max(1, int(max_attempts)))
        if delivered:
            event_store.mark_delivered(conn, event_id)
            dlq_store.delete_entry(conn, row_id)
            replayed += 1
        else:
            failed += 1
    return {
        "ok": failed == 0,
        "changed": replayed > 0,
        "detail": f"dlq_replayed={replayed} dlq_failed={failed}",
        "replayed": replayed,
        "failed": failed,
    }


def _restart_ingester(*, dry_run: bool) -> dict[str, Any]:
    command = ["systemctl", "try-restart", "csi-ingester.service"]
    if dry_run:
        return {"ok": True, "changed": True, "detail": "dry_run systemctl try-restart csi-ingester.service"}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=25, check=False)
        ok = int(result.returncode) == 0
        detail = f"exit={result.returncode} stdout={result.stdout[:240]} stderr={result.stderr[:240]}"
        return {"ok": ok, "changed": ok, "detail": detail}
    except Exception as exc:
        return {"ok": False, "changed": False, "detail": f"restart_exception type={type(exc).__name__} msg={exc}"}


def _build_event(
    *,
    config: CSIConfig,
    event_type: str,
    now_iso: str,
    remediation_summary: dict[str, Any],
    health: dict[str, Any],
) -> CreatorSignalEvent:
    suffix = uuid.uuid4().hex[:10]
    return CreatorSignalEvent(
        event_id=f"csi:auto_remediation:{event_type}:{config.instance_id}:{suffix}",
        dedupe_key=f"csi:auto_remediation:{event_type}:{config.instance_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}",
        source="csi_analytics",
        event_type=event_type,
        occurred_at=now_iso,
        received_at=now_iso,
        subject={
            "report_type": "delivery_health_auto_remediation",
            "status": str(remediation_summary.get("status") or ""),
            "health_status": str(health.get("status") or ""),
            "executed_actions": remediation_summary.get("executed_actions") or [],
            "skipped_actions": remediation_summary.get("skipped_actions") or [],
            "guardrails": remediation_summary.get("guardrails") or {},
            "failing_sources": health.get("failing_sources") or [],
            "degraded_sources": health.get("degraded_sources") or [],
        },
        routing={
            "pipeline": "csi_delivery_health_auto_remediation",
            "priority": "urgent" if event_type.endswith("_failed") else "standard",
            "tags": ["csi", "delivery_health", "auto_remediation", str(remediation_summary.get("status") or "")],
        },
        metadata={"source_adapter": "csi_delivery_health_auto_remediate_v1"},
    )


def _normalize_action_key(code: str, source: str) -> str:
    return f"{str(code or '').strip().lower()}::{str(source or 'global').strip().lower()}"


def _collect_actions(health: dict[str, Any]) -> list[dict[str, Any]]:
    steps = health.get("remediation_steps") if isinstance(health.get("remediation_steps"), list) else []
    actions: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        code = str(step.get("code") or "").strip().lower()
        source = str(step.get("source") or "").strip().lower()
        if not code:
            continue
        if code in {"delivery_failures_detected", "dlq_backlog_exceeds_threshold"}:
            actions.append({"handler": "replay_dlq", "code": code, "source": source or "delivery"})
        elif code in {"adapter_consecutive_failures", "rss_source_stale_or_low_volume", "reddit_source_stale_or_low_volume"}:
            actions.append({"handler": "restart_ingester", "code": code, "source": source or "ingester"})
    # always include proactive cursor correction pass for rss/reddit digest lanes
    actions.append({"handler": "cursor_reset_rss", "code": "digest_cursor_correction", "source": "youtube_channel_rss"})
    actions.append({"handler": "cursor_reset_reddit", "code": "digest_cursor_correction", "source": "reddit_discovery"})

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in actions:
        key = _normalize_action_key(str(row.get("code") or ""), str(row.get("source") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


async def _run_once(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    canary._apply_env_defaults(Path(args.csi_env_file).expanduser())
    canary._apply_env_defaults(Path(args.env_file).expanduser())
    env_vals = canary._load_env_file(Path(args.env_file).expanduser())
    env_vals.update(canary._load_env_file(Path(args.csi_env_file).expanduser()))

    tuning = {
        "window_hours": max(1, int(canary._resolve_setting(["UA_CSI_DELIVERY_CANARY_WINDOW_HOURS"], env_vals, "6"))),
        "stale_minutes": max(30, int(canary._resolve_setting(["UA_CSI_DELIVERY_STALE_MINUTES"], env_vals, "240"))),
        "max_failed_attempt_ratio": max(
            0.0,
            min(1.0, float(canary._resolve_setting(["UA_CSI_DELIVERY_MAX_FAILED_ATTEMPT_RATIO"], env_vals, "0.2"))),
        ),
        "min_rss_events": max(0, int(canary._resolve_setting(["UA_CSI_DELIVERY_MIN_RSS_EVENTS_24H"], env_vals, "1"))),
        "min_reddit_events": max(0, int(canary._resolve_setting(["UA_CSI_DELIVERY_MIN_REDDIT_EVENTS_24H"], env_vals, "1"))),
        "max_dlq_recent": max(0, int(canary._resolve_setting(["UA_CSI_DELIVERY_MAX_DLQ_RECENT"], env_vals, "0"))),
        "adapter_consecutive_failures": max(1, int(canary._resolve_setting(["UA_CSI_DELIVERY_ADAPTER_CONSECUTIVE_FAILURES"], env_vals, "3"))),
    }

    guardrails = {
        "cooldown_minutes": max(
            1,
            int(canary._resolve_setting(["UA_CSI_AUTO_REMEDIATE_COOLDOWN_MINUTES"], env_vals, str(args.cooldown_minutes))),
        ),
        "max_attempts_per_window": max(
            1,
            int(canary._resolve_setting(["UA_CSI_AUTO_REMEDIATE_MAX_ATTEMPTS_PER_WINDOW"], env_vals, str(args.max_attempts_per_window))),
        ),
        "attempt_window_minutes": max(
            10,
            int(canary._resolve_setting(["UA_CSI_AUTO_REMEDIATE_ATTEMPT_WINDOW_MINUTES"], env_vals, str(args.attempt_window_minutes))),
        ),
        "max_actions_per_run": max(
            1,
            int(canary._resolve_setting(["UA_CSI_AUTO_REMEDIATE_MAX_ACTIONS_PER_RUN"], env_vals, str(args.max_actions_per_run))),
        ),
        "dlq_replay_limit": max(1, int(canary._resolve_setting(["UA_CSI_AUTO_REMEDIATE_DLQ_REPLAY_LIMIT"], env_vals, str(args.dlq_replay_limit)))),
        "dlq_replay_attempts": max(1, int(canary._resolve_setting(["UA_CSI_AUTO_REMEDIATE_DLQ_REPLAY_ATTEMPTS"], env_vals, str(args.dlq_replay_attempts)))),
    }

    now_epoch = int(time.time())
    now_iso = _utc_now_iso()
    db_path = Path(args.db_path).expanduser()
    conn = connect(db_path)
    ensure_schema(conn)
    config = load_config()

    health = canary._evaluate_delivery_health(
        conn,
        window_hours=int(tuning["window_hours"]),
        stale_minutes=int(tuning["stale_minutes"]),
        max_failed_attempt_ratio=float(tuning["max_failed_attempt_ratio"]),
        min_rss_events=int(tuning["min_rss_events"]),
        min_reddit_events=int(tuning["min_reddit_events"]),
        max_dlq_recent=int(tuning["max_dlq_recent"]),
        adapter_failures_threshold=int(tuning["adapter_consecutive_failures"]),
    )
    if str(health.get("status") or "") not in {"degraded", "failing"} and not bool(args.force):
        conn.close()
        return 0, {"status": "noop", "reason": "health_ok", "health": health}

    state_key = str(args.state_key)
    state = source_state_store.get_state(conn, state_key) or {}
    actions = _collect_actions(health)
    executed_actions: list[dict[str, Any]] = []
    skipped_actions: list[dict[str, Any]] = []

    for action in actions:
        if len(executed_actions) >= int(guardrails["max_actions_per_run"]):
            skipped_actions.append({**action, "reason": "max_actions_per_run"})
            continue
        action_key = _normalize_action_key(str(action.get("code") or ""), str(action.get("source") or ""))
        allowed, reason = _guardrail_decision(
            state=state,
            action_key=action_key,
            now_epoch=now_epoch,
            cooldown_minutes=int(guardrails["cooldown_minutes"]),
            max_attempts_per_window=int(guardrails["max_attempts_per_window"]),
            attempt_window_minutes=int(guardrails["attempt_window_minutes"]),
        )
        if not allowed:
            skipped_actions.append({**action, "reason": reason})
            continue

        handler = str(action.get("handler") or "")
        result: dict[str, Any]
        if handler == "replay_dlq":
            result = await _replay_dlq(
                conn=conn,
                config=config,
                limit=int(guardrails["dlq_replay_limit"]),
                max_attempts=int(guardrails["dlq_replay_attempts"]),
                dry_run=bool(args.dry_run),
            )
        elif handler == "restart_ingester":
            result = _restart_ingester(dry_run=bool(args.dry_run))
        elif handler == "cursor_reset_rss":
            result = _cursor_reset_if_stale(
                conn=conn,
                state_path=Path(args.rss_state_path).expanduser(),
                source_name="youtube_channel_rss",
                dry_run=bool(args.dry_run),
            )
        elif handler == "cursor_reset_reddit":
            result = _cursor_reset_if_stale(
                conn=conn,
                state_path=Path(args.reddit_state_path).expanduser(),
                source_name="reddit_discovery",
                dry_run=bool(args.dry_run),
            )
        else:
            result = {"ok": False, "changed": False, "detail": f"unknown_handler:{handler}"}

        success = bool(result.get("ok"))
        _record_action_attempt(
            state=state,
            action_key=action_key,
            now_epoch=now_epoch,
            success=success,
            detail=str(result.get("detail") or ""),
            attempt_window_minutes=int(guardrails["attempt_window_minutes"]),
        )
        executed_actions.append({**action, "success": success, "result": result})

    state["last_run_epoch"] = now_epoch
    state["last_run_status"] = str(health.get("status") or "")
    source_state_store.set_state(conn, state_key, state)

    any_failed = any(not bool(item.get("success")) for item in executed_actions)
    any_changed = any(bool((item.get("result") or {}).get("changed")) for item in executed_actions)
    if not executed_actions:
        summary_status = "skipped"
    elif any_failed:
        summary_status = "failed"
    elif any_changed:
        summary_status = "succeeded"
    else:
        summary_status = "no_effect"

    summary = {
        "status": summary_status,
        "health_status": str(health.get("status") or ""),
        "executed_actions": executed_actions,
        "skipped_actions": skipped_actions,
        "guardrails": guardrails,
    }

    should_emit = bool(args.force) or bool(executed_actions) or bool(any_failed)
    emitted = False
    emit_status_code = 0
    if should_emit and not bool(args.dry_run):
        if summary_status == "failed":
            event_type = "delivery_health_auto_remediation_failed"
        elif summary_status in {"succeeded", "no_effect"}:
            event_type = "delivery_health_auto_remediation_succeeded"
        else:
            event_type = "delivery_health_auto_remediation_skipped"
        event = _build_event(
            config=config,
            event_type=event_type,
            now_iso=now_iso,
            remediation_summary=summary,
            health=health,
        )
        emitted, emit_status_code, _emit_payload = emit_and_track(conn, config=config, event=event, retry_count=3)
        summary["emitted"] = bool(emitted)
        summary["emit_status_code"] = int(emit_status_code or 0)

    conn.close()
    return (0 if summary_status != "failed" else 1), summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--state-key", default="runtime_canary:auto_remediation")
    parser.add_argument("--rss-state-path", default="/opt/universal_agent/CSI_Ingester/development/var/rss_digest_state.json")
    parser.add_argument("--reddit-state-path", default="/opt/universal_agent/CSI_Ingester/development/var/reddit_digest_state.json")
    parser.add_argument("--cooldown-minutes", type=int, default=30)
    parser.add_argument("--max-attempts-per-window", type=int, default=3)
    parser.add_argument("--attempt-window-minutes", type=int, default=360)
    parser.add_argument("--max-actions-per-run", type=int, default=3)
    parser.add_argument("--dlq-replay-limit", type=int, default=50)
    parser.add_argument("--dlq-replay-attempts", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    args = parser.parse_args()
    code, summary = asyncio.run(_run_once(args))
    print("CSI_AUTO_REMEDIATE", json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())

