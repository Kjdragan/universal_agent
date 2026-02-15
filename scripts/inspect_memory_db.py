
import sqlite3
import os
from datetime import datetime

DB_PATH = "Memory_System/data/agent_core.db"

def inspect_db():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at {DB_PATH}")
        return

    print(f"📂 Inspecting: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # List tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"📊 Tables: {[t[0] for t in tables]}")

    # Inspect archival_fts (Virtual Table)
    print("\n--- Recent Archival Memories (FTS) ---")
    try:
        # FTS doesn't have a timestamp column, only content/tags.
        # We can't sort by created_at easily unless we join with something else, 
        # but the storage manager doesn't seem to store timestamp in SQLite for archival.
        # So we just show the last inserted ones (rowid is usually chronological).
        cursor.execute("SELECT item_id, content, tags FROM archival_fts ORDER BY rowid DESC LIMIT 5")
        rows = cursor.fetchall()
        if not rows:
            print("No entries found in FTS.")
        for row in rows:
            print(f"ID: {row[0]}")
            print(f"Tags: {row[2]}")
            print(f"Content: {row[1][:100]}...")
            print("-" * 20)
    except Exception as e:
        print(f"Error querying archival_fts: {e}")

    # Inspect core_blocks
    print("\n--- Core Memory Blocks ---")
    try:
        cursor.execute("SELECT label, value, last_updated FROM core_blocks")
        rows = cursor.fetchall()
        if not rows:
            print("No entries found.")
        for row in rows:
            print(f"[{row[0]}] (Last Updated: {row[2]})")
            print(f"{row[1][:100]}...")
            print("-" * 20)
    except Exception as e:
        print(f"Error querying core_blocks: {e}")

    conn.close()

if __name__ == "__main__":
    inspect_db()
