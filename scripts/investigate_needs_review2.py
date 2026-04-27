#!/usr/bin/env python3
import json
import sqlite3
import sys

sys.path.insert(0, "/opt/universal_agent/src")
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

conn = connect_runtime_db(get_activity_db_path())
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT task_id, title, status, metadata_json FROM task_hub_items WHERE status = 'needs_review' LIMIT 10").fetchall()
res = [{"id": r["task_id"], "title": r["title"], "status": r["status"], "meta": json.loads(r["metadata_json"] or "{}")} for r in rows]
print(json.dumps(res, indent=2))
