#!/usr/bin/env python3
"""Emit scheduled global brief review reminders (notifications + personal Todo task)."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[3]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store.sqlite import connect, ensure_schema
from universal_agent.services.todoist_service import TodoService


@dataclass
class ReminderSlot:
    key: str
    hour: int
    minute: int
    display: str


SLOTS = (
    ReminderSlot(key="am0730", hour=7, minute=30, display="7:30 AM"),
    ReminderSlot(key="pm1700", hour=17, minute=0, display="5:00 PM"),
)


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, raw_val = item.split("=", 1)
        val = raw_val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _apply_env_defaults(path: Path) -> None:
    for key, value in _load_env_file(path).items():
        os.environ.setdefault(key, value)


def _load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _detect_slot(now_local: datetime, tolerance_minutes: int) -> ReminderSlot | None:
    now_minutes = now_local.hour * 60 + now_local.minute
    for slot in SLOTS:
        slot_minutes = slot.hour * 60 + slot.minute
        if abs(now_minutes - slot_minutes) <= max(0, int(tolerance_minutes)):
            return slot
    return None


def _latest_brief(conn: sqlite3.Connection) -> dict[str, str] | None:
    row = conn.execute(
        """
        SELECT brief_key, window_start_utc, window_end_utc, artifact_markdown_path, artifact_json_path, created_at
        FROM global_trend_briefs
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return {
        "brief_key": str(row["brief_key"] or ""),
        "window_start_utc": str(row["window_start_utc"] or ""),
        "window_end_utc": str(row["window_end_utc"] or ""),
        "artifact_markdown_path": str(row["artifact_markdown_path"] or ""),
        "artifact_json_path": str(row["artifact_json_path"] or ""),
        "created_at": str(row["created_at"] or ""),
    }


def _upsert_personal_todo(slot: ReminderSlot, local_date: str, brief: dict[str, str], timezone_name: str) -> None:
    svc = TodoService()
    brief_key = brief.get("brief_key") or "unknown"
    md_path = brief.get("artifact_markdown_path") or ""
    json_path = brief.get("artifact_json_path") or ""
    content = f"Review CSI global trend brief ({slot.display})"
    description = (
        f"Latest global briefing is ready for review.\n\n"
        f"brief_key: {brief_key}\n"
        f"window: {brief.get('window_start_utc')} -> {brief.get('window_end_utc')}\n"
        f"markdown: {md_path}\n"
        f"json: {json_path}\n"
        f"timezone: {timezone_name}\n"
        "intent: personal review reminder only (no auto execution)."
    )
    upsert_key = f"csi-global-brief-review:{local_date}:{slot.key}"
    due_string = f"today at {slot.display.lower()}"
    svc.create_personal_task(
        content=content,
        description=description,
        priority="high",
        section="scheduled",
        labels=["personal-reminder", "sleep-handoff", "no-auto-exec"],
        due_string=due_string,
        project_key="immediate",
        upsert_key=upsert_key,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Create scheduled CSI global brief review reminder")
    parser.add_argument("--db-path", default="/var/lib/universal-agent/csi/csi.db")
    parser.add_argument("--config-path", default="/opt/universal_agent/CSI_Ingester/development/config/config.yaml")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    parser.add_argument("--timezone", default="America/Chicago")
    parser.add_argument("--tolerance-minutes", type=int, default=7)
    parser.add_argument("--state-path", default="/var/lib/universal-agent/csi/global_brief_reminder_state.json")
    args = parser.parse_args()

    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())

    tz = ZoneInfo(str(args.timezone or "America/Chicago"))
    now_local = datetime.now(tz)
    slot = _detect_slot(now_local, args.tolerance_minutes)
    if slot is None:
        print("CSI_GLOBAL_BRIEF_REMINDER_SKIPPED=outside_slot")
        return 0

    local_date = now_local.strftime("%Y-%m-%d")
    state_path = Path(args.state_path).expanduser()
    state = _load_state(state_path)
    state_key = f"{local_date}:{slot.key}"
    if state.get("last_slot_key") == state_key:
        print(f"CSI_GLOBAL_BRIEF_REMINDER_SKIPPED=already_sent:{state_key}")
        return 0

    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)
    brief = _latest_brief(conn)
    if not brief:
        conn.close()
        print("CSI_GLOBAL_BRIEF_REMINDER_SKIPPED=no_brief")
        return 0

    todo_status = "ok"
    try:
        _upsert_personal_todo(slot, local_date, brief, str(args.timezone))
    except Exception as exc:
        todo_status = f"error:{type(exc).__name__}"

    cfg = load_config(Path(args.config_path).expanduser())
    now_utc = datetime.now(timezone.utc)
    event = CreatorSignalEvent(
        event_id=f"csi_global_brief_review_due:{local_date}:{slot.key}",
        dedupe_key=f"csi:global_brief_review_due:{local_date}:{slot.key}",
        source="csi_analytics",
        event_type="csi_global_brief_review_due",
        occurred_at=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        received_at=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        subject={
            "slot": slot.key,
            "slot_display": slot.display,
            "timezone": str(args.timezone),
            "local_date": local_date,
            "brief_key": brief.get("brief_key"),
            "window_start_utc": brief.get("window_start_utc"),
            "window_end_utc": brief.get("window_end_utc"),
            "artifact_paths": {
                "markdown": brief.get("artifact_markdown_path"),
                "json": brief.get("artifact_json_path"),
            },
            "message": f"Review latest CSI global trend brief ({slot.display} reminder).",
        },
        routing={"pipeline": "csi_analytics", "priority": "high", "tags": ["csi", "brief", "reminder"]},
        metadata={
            "brief_key": brief.get("brief_key"),
            "artifact_paths": {
                "markdown": brief.get("artifact_markdown_path"),
                "json": brief.get("artifact_json_path"),
            },
            "todo_status": todo_status,
        },
    )

    delivered, status_code, _ = emit_and_track(conn, config=cfg, event=event, retry_count=3)
    conn.close()

    state["last_slot_key"] = state_key
    state["last_brief_key"] = str(brief.get("brief_key") or "")
    state["last_sent_at_utc"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    _save_state(state_path, state)

    print(f"CSI_GLOBAL_BRIEF_REMINDER_SLOT={slot.key}")
    print(f"CSI_GLOBAL_BRIEF_REMINDER_BRIEF_KEY={brief.get('brief_key')}")
    print(f"CSI_GLOBAL_BRIEF_REMINDER_TODO_STATUS={todo_status}")
    print(f"CSI_GLOBAL_BRIEF_REMINDER_EMIT_DELIVERED={1 if delivered else 0}")
    print(f"CSI_GLOBAL_BRIEF_REMINDER_EMIT_STATUS={int(status_code or 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
