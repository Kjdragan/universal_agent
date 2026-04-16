#!/usr/bin/env python3
import sys
import os
import json
import sqlite3

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_path = os.path.join(repo_root, "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from universal_agent.durable.db import connect_runtime_db

def main():
    conn = connect_runtime_db()
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT task_id, title, source_kind, metadata_json 
        FROM task_hub_items 
        WHERE status = 'needs_review'
        ORDER BY updated_at DESC
        LIMIT 10
    """).fetchall()

    for r in rows:
        print("==========")
        print("Task ID:", r["task_id"])
        print("Title:", r["title"])
        print("Source Kind:", r["source_kind"])
        
        try:
            meta = json.loads(r["metadata_json"] or "{}")
            # Look at dispatch history, run errors, or reflection notes
            dispatch = meta.get("dispatch", {})
            print("Last Assignment State:", dispatch.get("last_assignment_state"))
            
            # Any block or review reasoning usually ends up in metadata or reflection_history logic, 
            # let's just dump the meta neatly.
            print("Metadata Dump:")
            print(json.dumps(meta, indent=2))
        except Exception as e:
            print("Could not parse metadata:", e)

if __name__ == "__main__":
    main()
