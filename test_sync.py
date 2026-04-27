from datetime import datetime
import json
import os
import sqlite3

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.services.proactive_convergence import get_topic_signature


def test():
    csi_path = "csi.db"
    db = sqlite3.connect(csi_path)
    db.row_factory = sqlite3.Row
    rows = db.execute("""
        SELECT e.event_id, e.subject_json, a.analyzed_at
        FROM events e
        LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
        WHERE e.source = 'youtube_channel_rss'
          AND a.summary_text IS NOT NULL
          AND a.summary_text != ''
        ORDER BY COALESCE(a.analyzed_at, e.occurred_at) DESC
        LIMIT 400
    """).fetchall()
    
    with connect_runtime_db(get_activity_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        new_count = 0
        for row in rows:
            subject = json.loads(row["subject_json"] or "{}")
            video_id = str(subject.get("video_id") or row["event_id"] or "").strip()
            if not video_id:
                continue
            if not get_topic_signature(conn, video_id):
                new_count += 1
    print(f"Total fetched: {len(rows)}, New to process: {new_count}")

if __name__ == "__main__":
    test()
