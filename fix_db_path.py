from pathlib import Path
import os
import sqlite3
import json
from datetime import datetime, timezone
import uuid

# Read the artifact we just generated
artifact_path = Path("/home/kjdragan/lrepos/universal_agent/workspaces/daily_digests/2026-04-30_MONDAY_Digest.md")
digest_content = artifact_path.read_text()

# Path the gateway actually uses
db_path = Path("/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/.csi_digests.db")

day_name = "MONDAY"
date_str = "2026-04-30"
video_count = 20

digest_id = str(uuid.uuid4())
event_id = f"yt_daily_digest_{date_str}_{day_name.lower()}"
title = f"Daily YouTube Digest: {day_name.title()}, {date_str} ({video_count} videos)"
summary = f"Processed {video_count} videos from the {day_name.title()} Digest playlist."

conn = sqlite3.connect(str(db_path), timeout=5)
conn.execute(
    "INSERT OR REPLACE INTO csi_digests "
    "(id, event_id, source, event_type, title, summary, full_report_md, source_types, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (
        digest_id,
        event_id,
        "youtube_daily_digest",
        "youtube_daily_digest",
        title,
        summary,
        digest_content,
        json.dumps(["youtube"]),
        datetime.now(timezone.utc).isoformat(),
    ),
)
conn.commit()
conn.close()
print(f"Emitted CSI digest record: {digest_id} to {db_path}")
