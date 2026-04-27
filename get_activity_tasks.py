import json
import os
import sqlite3
import sys

sys.path.insert(0, "./src")
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path


def main():
    db_path = get_activity_db_path()
    print(f"Opening {db_path}...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT task_id, title, status, metadata_json, updated_at FROM task_hub_items WHERE status = 'needs_review' ORDER BY updated_at DESC LIMIT 5").fetchall()
    
    if not rows:
        print("No stuck tasks found in activity db.")
        return
        
    for r in rows:
        print(f"Task: {r['title']} ({r['task_id']})")
        print(f"Updated: {r['updated_at']}")
        try:
            meta = json.loads(r['metadata_json']) if r['metadata_json'] else {}
            for k in ['todo_retry_count', 'workflow_run_id', 'agentmail_queue_id', 'assignment']:
                if k in meta: print(f"  {k}: {meta[k]}")
            if 'dispatch' in meta and 'last_assignment_state' in meta['dispatch']:
                print(f"  last_assignment_state: {meta['dispatch']['last_assignment_state']}")
            if 'history' in meta:
                 hist = meta['history']
                 if hist and isinstance(hist, list):
                      last = hist[-1]
                      print(f"  last history str: {json.dumps(last)[:400]}")
            if 'last_failed_evaluation' in meta:
                 print(f"  last_failed_evaluation: {json.dumps(meta['last_failed_evaluation'])[:400]}")
        except Exception as e:
             print(f"Error parse: {e}")
        print("-" * 50)

if __name__ == "__main__":
    main()
