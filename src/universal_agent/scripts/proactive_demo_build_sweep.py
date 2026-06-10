"""Cron entry point: queue CODIE tutorial-build tasks from build-oriented CSI YouTube videos.

Standalone systemd oneshot that runs the broad YouTube demo-build lane 3x/day,
decoupled from the dashboard's event-triggered proactive-signal sync
(gateway_server._run_proactive_signal_sync_background -> sync_generated_cards ->
sync_build_oriented_csi_videos). Calls
proactive_tutorial_builds.sync_build_oriented_csi_videos directly — the producer
already writes Task Hub rows (queue_tutorial_build_task -> queue_proactive_task ->
task_hub.upsert_item), so no extra cron-task-link wrapping is needed here. Each
queued tutorial-build row is itself the observable Task Hub artifact.

This is a NEW producer-invoker, not a migration of an existing registered cron —
there is no in-process twin and no double-fire gate. The tutorial-build:<sha256>
dedup makes overlapping runs (timer + dashboard event) idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.dormancy import should_run
from universal_agent.services.proactive_tutorial_builds import (
    sync_build_oriented_csi_videos,
)

DEFAULT_CSI_DB_PATH = "/var/lib/universal-agent/csi/csi.db"


def _csi_db_path() -> Path:
    return Path(os.getenv("CSI_DB_PATH", DEFAULT_CSI_DB_PATH)).expanduser()


def _limit() -> int:
    raw = str(os.getenv("UA_PROACTIVE_DEMO_BUILD_SWEEP_LIMIT", "200") or "200").strip()
    try:
        return max(1, min(int(raw), 1000))
    except ValueError:
        return 200


def _write_sync_report(payload: dict) -> Path:
    root = resolve_artifacts_dir() / "proactive" / "demo_build_sweep"
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "latest_sync.json"
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    return report_path


def main() -> int:
    # Runtime dormancy gate. The timer fires 3x/day inside the active window, but
    # gate per-run so a Persistent replay inside a deploy window can't fire
    # overnight. Default (env unset) stays windowed — set
    # UA_PROACTIVE_DEMO_BUILD_SWEEP_24_7=true to run 24/7. Gate BEFORE
    # initialize_runtime_secrets() so the overnight skip costs no Infisical
    # round-trip.
    run_24_7 = str(os.environ.get("UA_PROACTIVE_DEMO_BUILD_SWEEP_24_7", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not should_run(mode="always" if run_24_7 else "dormancy_aware"):
        print(json.dumps({"ok": True, "skipped": "dormant_window"}))
        return 0
    # Load Infisical secrets FIRST. This job runs as a standalone systemd oneshot
    # (no gateway parent to inherit ANTHROPIC/ZAI keys from), and the per-video
    # buildability LLM judge (proactive_tutorial_builds.is_video_buildable_with_judge,
    # gated by UA_TUTORIAL_BUILD_JUDGE_ENABLED) makes a bounded LLM call that reads
    # the API key from os.environ. Without this the judge fails closed and zero
    # build tasks are queued. Bare call so the unit's UA_DEPLOYMENT_PROFILE=vps
    # backstop drives a strict production load (dev resolves its own profile).
    initialize_runtime_secrets()
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
            payload["counts"] = sync_build_oriented_csi_videos(
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
