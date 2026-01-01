import sqlite3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  entrypoint TEXT NOT NULL,
  run_spec_json TEXT NOT NULL,
  current_step_id TEXT,
  last_checkpoint_id TEXT,
  final_artifact_ref TEXT
);

CREATE TABLE IF NOT EXISTS run_steps (
  step_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  step_index INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  error_code TEXT,
  error_detail TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
  tool_call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  tool_namespace TEXT NOT NULL,
  side_effect_class TEXT NOT NULL,
  normalized_args_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  request_ref TEXT,
  response_ref TEXT,
  external_correlation_id TEXT,
  error_code TEXT,
  error_detail TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(step_id) REFERENCES run_steps(step_id)
);

CREATE TABLE IF NOT EXISTS checkpoints (
  checkpoint_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  step_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  checkpoint_type TEXT NOT NULL,
  state_snapshot_json TEXT NOT NULL,
  cursor_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(step_id) REFERENCES run_steps(step_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run_step ON tool_calls(run_id, step_id);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, step_index);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
