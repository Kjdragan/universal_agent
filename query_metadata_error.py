import sys, sqlite3, json
sys.path.insert(0, "./src")
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

def main():
    conn = sqlite3.connect(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT task_id, title, metadata_json FROM task_hub_items WHERE status = 'needs_review' ORDER BY updated_at DESC LIMIT 1").fetchall()
    
    for r in rows:
        print(f"Task: {r['title']}")
        raw = r['metadata_json']
        if not raw:
            continue
        try:
            meta = json.loads(raw)
            print("Dispatch State:", meta.get("dispatch", {}))
            print("Last Assignment State:", meta.get("dispatch", {}).get("last_assignment_state"))
            for h in reversed(meta.get("history", [])):
                if h.get("state") == "failed":
                    print("Found failed history entry!")
                    print(json.dumps(h, indent=2))
                    break
        except Exception as e:
            print(f"Error parsing metadata: {e}")

if __name__ == "__main__":
    main()
