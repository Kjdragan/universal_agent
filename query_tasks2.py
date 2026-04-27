import json
import sqlite3
import sys

sys.path.insert(0, "./src")
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path


def main():
    db_path = get_activity_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT task_id, title, metadata_json FROM task_hub_items WHERE status = 'needs_review' ORDER BY updated_at DESC LIMIT 2").fetchall()
    
    for r in rows:
        print(f"Task: {r['title']}")
        try:
            meta = json.loads(r['metadata_json'])
            if 'dispatch' in meta:
                print("Dispatch:")
                print(json.dumps(meta['dispatch'], indent=2))
        except Exception as e:
            pass
        print("-" * 50)

if __name__ == "__main__":
    main()
