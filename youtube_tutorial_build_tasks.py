"""Diagnostic: list tutorial_build (YouTube -> demo) Task Hub items by date.

Companion to youtube_demo_report.sh. That script reports demos that were BUILT
(manifest on disk under /opt/ua_demos). This one reports the whole lane in the
Task Hub -- including videos that passed the gate but are still queued /
pending-approval / in-progress (no demo dir yet).

Run on the VPS, or anywhere the canonical activity_state.db resolves:
    uv run python youtube_tutorial_build_tasks.py
    SINCE=2026-06-15 UNTIL=2026-06-19 uv run python youtube_tutorial_build_tasks.py

Reads the canonical store via get_activity_db_path() (honors UA_ACTIVITY_DB_PATH),
never a hardcoded path. Each tutorial_build task carries video_id/video_title/
video_url in metadata_json; task_id = tutorial-build:sha256(video_id)[:16]
(see services/proactive_tutorial_builds.py::queue_tutorial_build_task).
"""

from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import sys

# src/ layout: make `universal_agent` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from universal_agent.durable.db import (  # noqa: E402  (after sys.path bootstrap)
    connect_runtime_db,
    get_activity_db_path,
)

SINCE = os.getenv("SINCE", "2026-06-15")  # Jun 15 = Sunday-playlist digest
UNTIL = os.getenv("UNTIL", "2026-06-19")

db_path = get_activity_db_path()
conn = connect_runtime_db(db_path)

rows = conn.execute(
    "SELECT task_id, title, status, source_ref, metadata_json, created_at, updated_at "
    "FROM task_hub_items WHERE task_id LIKE 'tutorial-build:%' "
    "ORDER BY created_at ASC;"
).fetchall()

by_date: dict[str, list] = defaultdict(list)
for r in rows:
    day = str(r["created_at"] or "")[:10]
    if SINCE <= day <= UNTIL:
        by_date[day].append(r)

total = sum(len(v) for v in by_date.values())
print(f"DB: {db_path}")
print(f"tutorial_build tasks created {SINCE}..{UNTIL}: {total}\n")

for day in sorted(by_date):
    print(f"=== {day} ===")
    for r in by_date[day]:
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except Exception:
            meta = {}
        title = meta.get("video_title") or r["title"] or "(no title)"
        vid = meta.get("video_id") or r["source_ref"] or ""
        url = meta.get("video_url") or ""
        print(f"  - [{r['status']}] {title}")
        print(f"      task_id:  {r['task_id']}")
        if vid or url:
            print(f"      video:    {vid}  {url}")
    print()
