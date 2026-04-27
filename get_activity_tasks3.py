import json
import sqlite3
import sys

sys.path.insert(0, "./src")
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path


def main():
    db_path = get_activity_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = dict_factory
    rows = conn.execute("SELECT * FROM task_hub_items WHERE status = 'needs_review' ORDER BY updated_at DESC LIMIT 1").fetchall()
    
    for r in rows:
        print(json.dumps(r, indent=2))

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

if __name__ == "__main__":
    main()
