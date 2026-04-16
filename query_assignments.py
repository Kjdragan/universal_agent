import sys, sqlite3, json
sys.path.insert(0, "./src")
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def main():
    conn = sqlite3.connect(get_activity_db_path())
    conn.row_factory = dict_factory
    rows = conn.execute("SELECT * FROM task_hub_assignments WHERE task_id = 'email:e6922a6f0326f292' ORDER BY started_at DESC LIMIT 3").fetchall()
    
    for r in rows:
        print(json.dumps(r, indent=2))
        print("-" * 50)

if __name__ == "__main__":
    main()
