import sqlite3
import threading

_SCHEMA_READY_PATHS: set[str] = set()
_SCHEMA_READY_LOCK = threading.Lock()


def _schema_cache_key(conn: sqlite3.Connection) -> str | None:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except Exception:
        return None
    for row in rows:
        name = row[1] if len(row) > 1 else None
        path = row[2] if len(row) > 2 else None
        if name == "main":
            normalized = str(path or "").strip()
            return normalized or None
    return None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  entrypoint TEXT NOT NULL,
  run_spec_json TEXT NOT NULL,
  workspace_dir TEXT,
  run_kind TEXT,
  trigger_source TEXT,
  dedup_key TEXT,
  run_policy TEXT,
  interrupt_policy TEXT,
  terminal_reason TEXT,
  attempt_count INTEGER DEFAULT 0,
  latest_attempt_id TEXT,
  last_success_attempt_id TEXT,
  canonical_attempt_id TEXT,
  external_origin TEXT,
  external_origin_id TEXT,
  external_correlation_id TEXT,
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
  cancel_reason TEXT,
  total_tokens INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_attempts (
  attempt_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  attempt_number INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  lease_owner TEXT,
  lease_expires_at TEXT,
  provider_session_id TEXT,
  started_at TEXT,
  ended_at TEXT,
  failure_class TEXT,
  failure_reason TEXT,
  retry_reason TEXT,
  retry_backoff_seconds INTEGER,
  workspace_subdir TEXT,
  summary_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  UNIQUE(run_id, attempt_number)
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

CREATE TABLE IF NOT EXISTS tool_receipts (
  tool_call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  tool_namespace TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  response_ref TEXT,
  external_correlation_id TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
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

CREATE TABLE IF NOT EXISTS vp_sessions (
  vp_id TEXT PRIMARY KEY,
  runtime_id TEXT NOT NULL,
  session_id TEXT,
  workspace_dir TEXT,
  status TEXT NOT NULL,
  lease_owner TEXT,
  lease_expires_at TEXT,
  last_heartbeat_at TEXT,
  last_error TEXT,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vp_missions (
  mission_id TEXT PRIMARY KEY,
  vp_id TEXT NOT NULL,
  run_id TEXT,
  status TEXT NOT NULL,
  mission_type TEXT,
  objective TEXT NOT NULL,
  budget_json TEXT,
  payload_json TEXT,
  result_ref TEXT,
  priority INTEGER DEFAULT 100,
  priority_tier TEXT NOT NULL DEFAULT 'background',
  worker_id TEXT,
  claim_expires_at TEXT,
  cancel_requested INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(vp_id) REFERENCES vp_sessions(vp_id)
);

CREATE TABLE IF NOT EXISTS vp_mission_backlog_history (
  sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
  measured_at TEXT NOT NULL,
  vp_id TEXT NOT NULL,
  priority_tier TEXT NOT NULL,
  queued_count INTEGER NOT NULL,
  running_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS vp_session_events (
  event_id TEXT PRIMARY KEY,
  vp_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(vp_id) REFERENCES vp_sessions(vp_id)
);

CREATE TABLE IF NOT EXISTS vp_events (
  event_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  vp_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(mission_id) REFERENCES vp_missions(mission_id),
  FOREIGN KEY(vp_id) REFERENCES vp_sessions(vp_id)
);

CREATE TABLE IF NOT EXISTS vp_bridge_cursors (
  cursor_key TEXT PRIMARY KEY,
  last_rowid INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run_step ON tool_calls(run_id, step_id);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, step_index);
CREATE INDEX IF NOT EXISTS idx_run_attempts_run ON run_attempts(run_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_run_attempts_status ON run_attempts(status, created_at);
CREATE INDEX IF NOT EXISTS idx_run_attempts_provider_session ON run_attempts(provider_session_id);
CREATE INDEX IF NOT EXISTS idx_tool_receipts_run ON tool_receipts(run_id);
CREATE INDEX IF NOT EXISTS idx_vp_sessions_status ON vp_sessions(status);
CREATE INDEX IF NOT EXISTS idx_vp_missions_vp_status ON vp_missions(vp_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_vp_events_mission ON vp_events(mission_id, created_at);
CREATE INDEX IF NOT EXISTS idx_vp_events_vp ON vp_events(vp_id, created_at);
CREATE INDEX IF NOT EXISTS idx_vp_session_events_vp ON vp_session_events(vp_id, created_at);
CREATE INDEX IF NOT EXISTS idx_vp_session_events_type ON vp_session_events(event_type, created_at);
-- idx_vp_missions_tier_priority + idx_vp_backlog_history_recent are created in
-- ensure_schema() AFTER the priority_tier ALTER TABLE backfill runs. They can't
-- live here because executescript() runs this block before the ALTER, and on
-- pre-PR-499 databases vp_missions.priority_tier doesn't yet exist, so SQLite
-- aborts the entire script at the index. See 2026-05-27 incident postmortem.

CREATE TABLE IF NOT EXISTS user_preferences (
  user_id TEXT PRIMARY KEY,
  preferences_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


# SQLite column-type declarations passed to ``_add_column_if_missing`` during
# additive migrations. Named (not inline) so each type and each sentinel
# default has exactly one source of truth: these args are only exercised when
# migrating a PRE-EXISTING database that predates the column, so a typo in an
# inline literal would silently create a mistyped/mis-defaulted column with no
# fresh-DB test catching it. Values are byte-identical to the inline literals
# they replace.
_COL_TEXT = "TEXT"
_COL_INTEGER = "INTEGER"
_COL_INTEGER_DEFAULT_ZERO = "INTEGER DEFAULT 0"
_COL_INTEGER_DEFAULT_100 = "INTEGER DEFAULT 100"
_COL_TEXT_DEFAULT_GATEWAY = "TEXT DEFAULT 'gateway'"
_COL_TEXT_NOT_NULL_DEFAULT_REPLAY_EXACT = "TEXT NOT NULL DEFAULT 'REPLAY_EXACT'"
_COL_TEXT_NOT_NULL_DEFAULT_BACKGROUND = "TEXT NOT NULL DEFAULT 'background'"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, column_type: str
) -> None:
    if _column_exists(conn, table, column):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _backfill_vp_mission_priority_tier(conn: sqlite3.Connection) -> None:
    """One-time backfill of vp_missions.priority_tier from mission_type.

    Idempotent: the WHERE clause only touches rows still at the default
    'background' tier, so once a row is moved into 'operator_daily' /
    'operator_signal' / 'maintenance' it stays there (manual operator
    overrides survive subsequent calls).

    Re-imports the mapping from ``vp.mission_priority`` so the source of
    truth stays in one place.
    """
    try:
        from universal_agent.vp.mission_priority import MISSION_TYPE_TIER
    except Exception:
        # If the constants module ever moves or breaks import, leave
        # rows at the safe default rather than crashing schema setup.
        return
    if not MISSION_TYPE_TIER:
        return
    # Build a single UPDATE per tier so we issue at most 4 statements
    # regardless of how many mission_types map to each tier.
    by_tier: dict[str, list[str]] = {}
    for mission_type, tier in MISSION_TYPE_TIER.items():
        by_tier.setdefault(tier, []).append(mission_type)
    for tier, mission_types in by_tier.items():
        if tier == "background":
            continue  # column default already covers this case
        placeholders = ",".join("?" * len(mission_types))
        conn.execute(
            f"""
            UPDATE vp_missions
            SET priority_tier = ?
            WHERE priority_tier = 'background'
              AND mission_type IN ({placeholders})
            """,
            (tier, *mission_types),
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    cache_key = _schema_cache_key(conn)
    if cache_key:
        with _SCHEMA_READY_LOCK:
            if cache_key in _SCHEMA_READY_PATHS:
                return
    conn.executescript(SCHEMA_SQL)
    _add_column_if_missing(conn, "runs", "workspace_dir", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "run_kind", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "trigger_source", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "dedup_key", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "run_policy", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "interrupt_policy", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "terminal_reason", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "attempt_count", _COL_INTEGER_DEFAULT_ZERO)
    _add_column_if_missing(conn, "runs", "latest_attempt_id", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "last_success_attempt_id", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "canonical_attempt_id", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "external_origin", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "external_origin_id", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "external_correlation_id", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "run_mode", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "job_path", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "last_job_prompt", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "provider_session_id", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "provider_session_forked_from", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "provider_session_last_seen_at", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "parent_run_id", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "lease_owner", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "lease_expires_at", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "last_heartbeat_at", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "cancel_requested_at", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "cancel_reason", _COL_TEXT)
    _add_column_if_missing(conn, "tool_calls", "raw_tool_name", _COL_TEXT)
    _add_column_if_missing(
        conn, "tool_calls", "replay_policy", _COL_TEXT_NOT_NULL_DEFAULT_REPLAY_EXACT
    )
    _add_column_if_missing(conn, "tool_calls", "replay_status", _COL_TEXT)
    _add_column_if_missing(conn, "tool_calls", "policy_matched", _COL_INTEGER)
    _add_column_if_missing(conn, "tool_calls", "policy_rule_id", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "iteration_count", _COL_INTEGER_DEFAULT_ZERO)
    _add_column_if_missing(conn, "runs", "max_iterations", _COL_INTEGER)
    _add_column_if_missing(conn, "runs", "completion_promise", _COL_TEXT)
    _add_column_if_missing(conn, "runs", "total_tokens", _COL_INTEGER_DEFAULT_ZERO)
    # Corpus cache for sub-agent context restoration
    _add_column_if_missing(conn, "checkpoints", "corpus_data", _COL_TEXT)
    _add_column_if_missing(conn, "vp_missions", "mission_type", _COL_TEXT)
    _add_column_if_missing(conn, "vp_missions", "payload_json", _COL_TEXT)
    _add_column_if_missing(conn, "vp_missions", "priority", _COL_INTEGER_DEFAULT_100)
    _add_column_if_missing(
        conn, "vp_missions", "priority_tier", _COL_TEXT_NOT_NULL_DEFAULT_BACKGROUND
    )
    _backfill_vp_mission_priority_tier(conn)
    # Tier-aware indexes — must run AFTER priority_tier exists on the (possibly
    # pre-existing) vp_missions table. See SCHEMA_SQL comment above for context.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vp_missions_tier_priority "
        "ON vp_missions(vp_id, status, priority_tier, priority, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vp_backlog_history_recent "
        "ON vp_mission_backlog_history(measured_at DESC, vp_id, priority_tier)"
    )
    _add_column_if_missing(conn, "vp_missions", "worker_id", _COL_TEXT)
    _add_column_if_missing(conn, "vp_missions", "claim_expires_at", _COL_TEXT)
    _add_column_if_missing(
        conn, "vp_missions", "cancel_requested", _COL_INTEGER_DEFAULT_ZERO
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vp_missions_vp_claim ON vp_missions(vp_id, claim_expires_at)"
    )
    # Phase 3a: Redis→SQLite bridge columns
    _add_column_if_missing(conn, "vp_missions", "source", _COL_TEXT_DEFAULT_GATEWAY)
    _add_column_if_missing(
        conn, "vp_missions", "result_published", _COL_INTEGER_DEFAULT_ZERO
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vp_missions_bridge_results "
        "ON vp_missions(source, result_published, status)"
    )
    conn.commit()
    if cache_key:
        with _SCHEMA_READY_LOCK:
            _SCHEMA_READY_PATHS.add(cache_key)
