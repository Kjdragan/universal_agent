import sys
sys.path.insert(0, "/home/kjdragan/lrepos/universal_agent/src")
from universal_agent.durable.db import get_task_hub_db_path
import sqlite3

db_path = get_task_hub_db_path()
print("DB Path:", db_path)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT task_id, title, status FROM task_hub_items WHERE status IN ('needs_review', 'pending_review') ORDER BY updated_at DESC LIMIT 20;").fetchall()
print(f"Found {len(rows)} tasks needing review.")
for row in rows:
    print(f"- [{row['status']}] {row['task_id']}: {row['title']}")

