"""Cron entry point for CSI convergence producer sync."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.services.proactive_convergence import (
    sync_topic_signatures_from_csi,
)

DEFAULT_CSI_DB_PATH = "/var/lib/universal-agent/csi/csi.db"


def _csi_db_path() -> Path:
    return Path(os.getenv("CSI_DB_PATH", DEFAULT_CSI_DB_PATH)).expanduser()


def _limit() -> int:
    raw = str(os.getenv("UA_CSI_CONVERGENCE_SYNC_LIMIT", "400") or "400").strip()
    try:
        return max(1, min(int(raw), 2000))
    except ValueError:
        return 400


def _write_sync_report(payload: dict) -> Path:
    root = resolve_artifacts_dir() / "proactive" / "csi_convergence"
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "latest_sync.json"
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return report_path


def main() -> int:
    csi_path = _csi_db_path()
    payload = {
        "ok": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "csi_db_path": str(csi_path),
        "counts": {},
        "error": "",
        "report_path": "",
    }
    try:
        with connect_runtime_db(get_activity_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            payload["counts"] = sync_topic_signatures_from_csi(
                conn,
                csi_db_path=csi_path,
                limit=_limit(),
            )
        payload["ok"] = True
        report_path = _write_sync_report(payload)
        payload["report_path"] = str(report_path)
        report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0
    except Exception as exc:
        payload["error"] = str(exc)
        report_path = _write_sync_report(payload)
        payload["report_path"] = str(report_path)
        print(json.dumps(payload, indent=2, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
