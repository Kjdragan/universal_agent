import sqlite3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  entrypoint TEXT NOT NULL,
  run_spec_json TEXT NOT NULL,
  run_mode TEXT,
  job_path TEXT,
  last_job_prompt TEXT,
  provider_session_id TEXT,
  provider_session_forked_from TEXT,
  provider_session_last_seen_at TEXT,
  parent_run_id TEXT,
  current_step_id TEXT,
  last_checkpoint_id TEXT,
  final_artifact_ref TEXT,
  lease_owner TEXT,
  lease_expires_at TEXT,
  last_heartbeat_at TEXT,
  cancel_requested_at TEXT,
  cancel_reason TEXT
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
  raw_tool_name TEXT,
  tool_name TEXT NOT NULL,
  tool_namespace TEXT NOT NULL,
  side_effect_class TEXT NOT NULL,
  replay_policy TEXT NOT NULL DEFAULT 'REPLAY_EXACT',
  replay_status TEXT,
  policy_matched INTEGER,
  policy_rule_id TEXT,
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


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, column_type: str
) -> None:
    if _column_exists(conn, table, column):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _add_column_if_missing(conn, "runs", "run_mode", "TEXT")
    _add_column_if_missing(conn, "runs", "job_path", "TEXT")
    _add_column_if_missing(conn, "runs", "last_job_prompt", "TEXT")
    _add_column_if_missing(conn, "runs", "provider_session_id", "TEXT")
    _add_column_if_missing(conn, "runs", "provider_session_forked_from", "TEXT")
    _add_column_if_missing(conn, "runs", "provider_session_last_seen_at", "TEXT")
    _add_column_if_missing(conn, "runs", "parent_run_id", "TEXT")
    _add_column_if_missing(conn, "runs", "lease_owner", "TEXT")
    _add_column_if_missing(conn, "runs", "lease_expires_at", "TEXT")
    _add_column_if_missing(conn, "runs", "last_heartbeat_at", "TEXT")
    _add_column_if_missing(conn, "runs", "cancel_requested_at", "TEXT")
    _add_column_if_missing(conn, "runs", "cancel_reason", "TEXT")
    _add_column_if_missing(conn, "tool_calls", "raw_tool_name", "TEXT")
    _add_column_if_missing(
        conn, "tool_calls", "replay_policy", "TEXT NOT NULL DEFAULT 'REPLAY_EXACT'"
    )
    _add_column_if_missing(conn, "tool_calls", "replay_status", "TEXT")
    _add_column_if_missing(conn, "tool_calls", "policy_matched", "INTEGER")
    _add_column_if_missing(conn, "tool_calls", "policy_rule_id", "TEXT")
    conn.commit()
