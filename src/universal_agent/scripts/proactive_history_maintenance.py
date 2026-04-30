"""Classify and optionally backfill Proactive Task History legacy records.

This script is intentionally non-destructive.  Dry-run is the default.  With
``--apply`` it annotates Task Hub metadata so legacy/noisy rows can be audited
or hidden by UI policy later without deleting task/session evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.durable.db import get_activity_db_path

DEFAULT_CUTOVER_ISO = "2026-04-30T00:00:00-05:00"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def classify_proactive_history_item(
    item: dict[str, Any],
    *,
    cutover: datetime,
    recap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a maintenance recommendation for one proactive history row."""

    source_kind = str(item.get("source_kind") or "").strip().lower()
    status = str(item.get("status") or "").strip().lower()
    updated_at = _parse_dt(str(item.get("updated_at") or "")) or datetime.min.replace(tzinfo=timezone.utc)
    metadata = _metadata(item)
    dispatch = metadata.get("dispatch") if isinstance(metadata.get("dispatch"), dict) else {}
    result_text = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("description") or ""),
            str(dispatch.get("last_disposition_reason") or ""),
            str((item.get("last_assignment") or {}).get("result_summary") if isinstance(item.get("last_assignment"), dict) else ""),
        ]
    ).lower()
    recap_status = str((recap or {}).get("evaluation_status") or "").strip().lower()

    reasons: list[str] = []
    action = "keep_current"
    hidden_by_default = False

    if "heartbeat" in source_kind:
        action = "archive_noise"
        hidden_by_default = True
        reasons.append("heartbeat_health_check_noise")
    if updated_at < cutover and status in {task_hub.TASK_STATUS_PARKED, task_hub.TASK_STATUS_CANCELLED}:
        action = "archive_legacy"
        hidden_by_default = True
        reasons.append("pre_cutover_terminal_legacy")
    if "duplicate" in result_text:
        action = "archive_duplicate"
        hidden_by_default = True
        reasons.append("duplicate_or_sister_task")
    if "completion_claim_missing_email_delivery" in result_text or "stale_assignment_timeout" in result_text:
        action = "investigate"
        reasons.append("historical_lifecycle_or_delivery_failure")
    if status in {task_hub.TASK_STATUS_COMPLETED, task_hub.TASK_STATUS_PARKED} and not recap_status:
        if action == "keep_current":
            action = "backfill_recap"
        reasons.append("missing_durable_recap")
    elif "failed" in recap_status:
        if action == "keep_current":
            action = "investigate"
        reasons.append("recap_evaluator_failed")

    return {
        "task_id": str(item.get("task_id") or ""),
        "source_kind": source_kind,
        "status": status,
        "action": action,
        "hidden_by_default": hidden_by_default,
        "reasons": reasons,
        "updated_at": str(item.get("updated_at") or ""),
        "title": str(item.get("title") or ""),
    }


def _write_classification(conn: sqlite3.Connection, item: dict[str, Any], classification: dict[str, Any]) -> None:
    metadata = _metadata(item)
    metadata["proactive_history_maintenance"] = {
        **classification,
        "classified_at": _now_iso(),
        "tool": "proactive_history_maintenance",
    }
    if classification.get("hidden_by_default"):
        metadata["proactive_history_hidden_by_default"] = True
    conn.execute(
        "UPDATE task_hub_items SET metadata_json = ?, updated_at = ? WHERE task_id = ?",
        (
            json.dumps(metadata, ensure_ascii=True, sort_keys=True),
            _now_iso(),
            classification["task_id"],
        ),
    )


def run(
    *,
    db_path: str,
    cutover_iso: str = DEFAULT_CUTOVER_ISO,
    apply: bool = False,
    backfill_recaps: bool = False,
    limit: int = 500,
) -> dict[str, Any]:
    cutover = _parse_dt(cutover_iso)
    if cutover is None:
        raise ValueError(f"Invalid cutover timestamp: {cutover_iso!r}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tasks = task_hub.list_proactive_work_tasks(conn, limit=limit)
        from universal_agent.services.proactive_work_recap import get_recap_for_task, upsert_recap_for_task

        classifications: list[dict[str, Any]] = []
        backfilled: list[str] = []
        for item in tasks:
            task_id = str(item.get("task_id") or "")
            recap = get_recap_for_task(conn, task_id) if task_id else None
            classification = classify_proactive_history_item(item, cutover=cutover, recap=recap)
            classifications.append(classification)
            if apply:
                _write_classification(conn, item, classification)
            if (
                apply
                and backfill_recaps
                and classification["action"] == "backfill_recap"
                and task_id
            ):
                upsert_recap_for_task(
                    conn,
                    task_id=task_id,
                    terminal_action=str(item.get("status") or "terminal"),
                    reason="proactive_history_maintenance_backfill",
                    agent_id="system.proactive_history_maintenance",
                )
                backfilled.append(task_id)
        if apply:
            conn.commit()

        summary: dict[str, int] = {}
        for classification in classifications:
            action = str(classification.get("action") or "unknown")
            summary[action] = summary.get(action, 0) + 1
        return {
            "db_path": db_path,
            "cutover": cutover.isoformat(),
            "applied": apply,
            "backfill_recaps": backfill_recaps,
            "summary": summary,
            "backfilled_task_ids": backfilled,
            "classifications": classifications,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=get_activity_db_path(), help="Path to activity_state.db")
    parser.add_argument("--cutover", default=DEFAULT_CUTOVER_ISO, help="Pre-cutover timestamp for legacy classification")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--apply", action="store_true", help="Write maintenance metadata to task_hub_items")
    parser.add_argument("--backfill-recaps", action="store_true", help="Generate missing recaps for eligible terminal tasks; requires --apply")
    args = parser.parse_args()
    if args.backfill_recaps and not args.apply:
        raise SystemExit("--backfill-recaps requires --apply")
    result = run(
        db_path=args.db,
        cutover_iso=args.cutover,
        apply=bool(args.apply),
        backfill_recaps=bool(args.backfill_recaps),
        limit=max(1, int(args.limit)),
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
