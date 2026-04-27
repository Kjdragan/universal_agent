import os
import sqlite3
import sys
from typing import Optional


def _task_hub_open_conn() -> Optional[sqlite3.Connection]:
    try:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        from universal_agent.gateway_server import _task_hub_open_conn
        return _task_hub_open_conn()
    except Exception as e:
        print(f"Failed to load task database connection: {e}")
        return None

def clear_agent_queue():
    print("Connecting to the Activity/TaskHub database...")
    conn = _task_hub_open_conn()
    if not conn:
        print("Could not connect to the database. Are you running this via 'uv run' and PYTHONPATH=src?")
        sys.exit(1)
        
    cur = conn.cursor()
    
    cur.execute("SELECT count(*) FROM task_hub_items WHERE status IN ('open', 'in_progress', 'blocked', 'needs_review')")
    count = cur.fetchone()[0]
    
    print(f"Found {count} item(s) in the agent/personal queue.")
    
    if count > 0:
        print("Clearing items by marking them as status='completed'...")
        cur.execute("""
            UPDATE task_hub_items 
            SET status = 'completed', 
                seizure_state = 'completed',
                updated_at = datetime('now') 
            WHERE status IN ('open', 'in_progress', 'blocked', 'needs_review')
        """)
        
        # Also finalize any remaining assignments
        cur.execute("""
            UPDATE task_hub_assignments
            SET state='completed', ended_at=datetime('now'), result_summary='cleared via declaration of bankruptcy'
            WHERE state IN ('seized', 'running')
        """)
        
        conn.commit()
        print(f"Successfully cleared {count} task(s). The database has been reset for these tasks.")
    else:
        print("The agent queue is already empty.")
        
    conn.close()

if __name__ == "__main__":
    clear_agent_queue()
