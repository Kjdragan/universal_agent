import sqlite3
import json

def get_csi_data():
    conn = sqlite3.connect('/opt/universal_agent/CSI_Ingester/development/var/csi.db')
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [dict(row) for row in cur.fetchall()]
        print(tables)
        
        if any(t['name'] == 'insight_reports' for t in tables):
            cur.execute("SELECT * FROM insight_reports ORDER BY created_at DESC LIMIT 10")
            print([dict(r) for r in cur.fetchall()])
    finally:
        conn.close()

get_csi_data()
