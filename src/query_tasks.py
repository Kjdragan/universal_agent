"""Ad-hoc diagnostic: list Task Hub items awaiting review in the canonical store.

Run from the repo root, e.g. ``uv run python src/query_tasks.py``. Reads the
canonical ``activity_state.db`` via the shared, cwd-independent resolver
(``get_activity_db_path``); honors ``UA_ACTIVITY_DB_PATH`` when set. The previous
version imported a non-existent ``get_task_hub_db_path`` and targeted the stale
orphan ``task_hub.db`` — both fixed here.
"""

from pathlib import Path
import sys

# Make `universal_agent` importable when run directly under the src/ layout,
# portably — no hardcoded machine path (query_tasks.py lives at src/).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from universal_agent.durable.db import (  # noqa: E402  (after sys.path bootstrap)
    connect_runtime_db,
    get_activity_db_path,
)

db_path = get_activity_db_path()
print("DB Path:", db_path)

conn = connect_runtime_db(db_path)  # sets row_factory=Row + WAL + busy-timeout

rows = conn.execute(
    "SELECT task_id, title, status FROM task_hub_items "
    "WHERE status IN ('needs_review', 'pending_review') "
    "ORDER BY updated_at DESC LIMIT 20;"
).fetchall()
print(f"Found {len(rows)} tasks needing review.")
for row in rows:
    print(f"- [{row['status']}] {row['task_id']}: {row['title']}")
