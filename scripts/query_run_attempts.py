#!/usr/bin/env python3
import sys, os, sqlite3, json
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(repo_root, "src"))
from universal_agent.durable.db import connect_runtime_db

def main():
    conn = connect_runtime_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT task_id, title, status, metadata_json, updated_at FROM task_hub_items WHERE status = 'needs_review' ORDER BY updated_at DESC LIMIT 5").fetchall()
    if not rows:
        print("No stuck tasks found.")
        return
    for r in rows:
        print(f"Task: {r['title']} ({r['task_id']})")
        print(f"Updated: {r['updated_at']}")
        try:
            meta = json.loads(r['metadata_json']) if r['metadata_json'] else {}
            print(f"Metadata summary:")
            for k in ['todo_retry_count', 'workflow_run_id', 'agentmail_queue_id', 'assignment']:
                if k in meta: print(f"  {k}: {meta[k]}")
            if 'last_failed_evaluation' in meta:
                print(f"  last_failed_evaluation: {str(meta['last_failed_evaluation'])[:200]}...")
        except Exception as e:
            print(f"Error parsing metadata: {e}")
        print("-" * 50)

if __name__ == "__main__":
    main()
