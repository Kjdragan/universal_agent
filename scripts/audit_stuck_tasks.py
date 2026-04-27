#!/usr/bin/env python3
import os
import sys

# Ensure src/ is on the path so we can import internal modules
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_path = os.path.join(repo_root, "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

try:
    from dotenv import load_dotenv
    # Load the `.env` explicitly just in case it's run standalone
    load_dotenv(os.path.join(repo_root, ".env"))
except ImportError:
    pass

from collections import defaultdict
import sqlite3

from universal_agent.durable.db import connect_runtime_db


def _time_since(updated_at_iso: str) -> str:
    # A simple helper to show age if wanted, but printing raw is fine
    return updated_at_iso

def main():
    print("Connecting to Universal Agent Database...")
    try:
        conn = connect_runtime_db()
        conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"Failed to connect to database: {e}")
        return

    try:
        query = """
            SELECT task_id, title, status, source_kind, updated_at
            FROM task_hub_items
            WHERE status IN ('needs_review', 'pending_review')
            ORDER BY updated_at ASC
        """
        rows = conn.execute(query).fetchall()
    except Exception as e:
        print(f"Failed to query task_hub_items: {e}")
        return

    if not rows:
        print("✅ No tasks currently stuck in 'needs_review' or 'pending_review' in this database context.")
        print("Note: If the dashboard shows tasks, ensure you are running this script in the correct environment (VPS vs Local) or that the environment variables (UA_RUNTIME_DB_PATH) match what the Web UI gateway consumes.")
        return

    print(f"\nFound {len(rows)} tasks staged in review lanes:")
    
    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1
        print(f"  - [{r['status']}] {r['task_id'][:12]}... (Modified: {r['updated_at']})")
        print(f"    Source: {r['source_kind']}")
        print(f"    Title: {r['title']}")
        print("-" * 50)
        
    print("\n=== Summary ===")
    print(f"Human-in-the-Loop Required ('needs_review'): {counts.get('needs_review', 0)}")
    print(f"Agent Autonomous Staging ('pending_review'): {counts.get('pending_review', 0)}")

if __name__ == "__main__":
    main()
