#!/usr/bin/env python3
"""Query all needs_review tasks and produce a diagnostic report."""
import sqlite3
import json
import os
import glob

def find_db():
    db_path = os.environ.get("UA_ACTIVITY_DB_PATH", "/opt/universal_agent/data/activity_state.db")
    if os.path.exists(db_path):
        return db_path
    candidates = glob.glob("/opt/universal_agent*/**/activity_state.db", recursive=True)
    if candidates:
        return candidates[0]
    return None

def main():
    db_path = find_db()
    if not db_path:
        print("ERROR: activity_state.db not found")
        return

    print(f"DB: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT task_id, title, status, source_kind, metadata_json, created_at, updated_at "
        "FROM task_hub_items WHERE status = 'needs_review' ORDER BY updated_at DESC"
    ).fetchall()

    print(f"Total needs_review tasks: {len(rows)}\n")

    for i, row in enumerate(rows, 1):
        task_id = row["task_id"]
        metadata = json.loads(row["metadata_json"] or "{}")
        dispatch = metadata.get("dispatch", {})
        manifest = metadata.get("workflow_manifest", {})

        print(f"=== Card #{i} ===")
        print(f"  task_id: {task_id}")
        print(f"  title: {str(row['title'] or '')[:120]}")
        print(f"  source_kind: {row['source_kind']}")
        print(f"  created_at: {row['created_at']}")
        print(f"  updated_at: {row['updated_at']}")
        print(f"  last_disposition: {dispatch.get('last_disposition', 'N/A')}")
        print(f"  last_disposition_reason: {dispatch.get('last_disposition_reason', 'N/A')}")
        print(f"  completion_unverified: {dispatch.get('completion_unverified', 'N/A')}")
        print(f"  delivery_mode: {manifest.get('delivery_mode', metadata.get('delivery_mode', 'N/A'))}")
        print(f"  final_channel: {manifest.get('final_channel', 'N/A')}")
        print(f"  canonical_executor: {manifest.get('canonical_executor', 'N/A')}")
        print(f"  heartbeat_retry_count: {dispatch.get('heartbeat_retry_count', 'N/A')}")
        print(f"  todo_retry_count: {dispatch.get('todo_retry_count', 'N/A')}")

        outbound = dispatch.get("outbound_delivery", {})
        if outbound:
            print(f"  outbound_delivery: channel={outbound.get('channel','')}, sent_at={outbound.get('sent_at','')}, message_id={outbound.get('message_id','')}")
        else:
            print(f"  outbound_delivery: NONE")

        # Check assignments
        assignments = conn.execute(
            "SELECT assignment_id, agent_id, state, started_at, ended_at, result_summary, workflow_run_id "
            "FROM task_hub_assignments WHERE task_id = ? ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 3",
            (task_id,),
        ).fetchall()

        if not assignments:
            print("  assignments: NONE")
        for j, a in enumerate(assignments):
            agent = str(a["agent_id"] or "")[:60]
            print(f"  assignment[{j}]: state={a['state']}, agent={agent}, ended={a['ended_at'] or 'N/A'}")
            if a["result_summary"]:
                print(f"    result_summary: {str(a['result_summary'])[:300]}")

        # Check evaluations
        evals = conn.execute(
            "SELECT decision, reason, score FROM task_hub_evaluations WHERE task_id = ? ORDER BY evaluated_at DESC LIMIT 1",
            (task_id,),
        ).fetchall()
        for e in evals:
            print(f"  eval: decision={e['decision']}, score={e['score']}, reason={str(e['reason'])[:200]}")

        print()

    conn.close()

if __name__ == "__main__":
    main()
