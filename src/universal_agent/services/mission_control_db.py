"""Mission Control Intelligence System — durable storage layer.

Phase 0 scaffolding. Defines the SQLite schema for the four tables that
back the tiered intelligence system:

  - mission_control_cards          (tier-1 narrative cards)
  - mission_control_tile_states    (tier-0 traffic-light tiles)
  - mission_control_dispatch_history (action-button audit trail)
  - event_title_templates          (Phase 7 — LLM-generated title cache)

This module is import-safe and side-effect free until `open_store()` or
`ensure_schema()` is explicitly called. The sweeper service (Phase 1+)
opens the store on first tick; tests open the store directly.

The schema enforces the no-truncation contract by storing all
operator-facing free-form text as TEXT columns with no length limit.
Storage management lives at the retention boundary, not at collection.

See docs/02_Subsystems/Mission_Control_Intelligence_System.md §3.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from universal_agent.durable.db import get_sqlite_busy_timeout_ms

DEFAULT_DB_FILENAME = "mission_control_intelligence.db"


def _workspace_root() -> Path:
    """Mirror of the workspace-root helper used by Chief-of-Staff."""
    configured = os.getenv("UA_WORKSPACES_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    # services/mission_control_db.py -> services -> universal_agent -> src -> repo
    return (Path(__file__).resolve().parents[3] / "AGENT_RUN_WORKSPACES").resolve()


def default_db_path() -> Path:
    configured = os.getenv("UA_MISSION_CONTROL_INTEL_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    root = _workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / DEFAULT_DB_FILENAME


def open_store(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and lazily initialize) the Mission Control intelligence DB.

    Returns an autocommit-style connection (isolation_level=None) with
    WAL journaling — same pattern as the Chief-of-Staff store, so
    concurrent reads from the gateway and the sweeper are safe.
    """
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=get_sqlite_busy_timeout_ms() / 1000.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={get_sqlite_busy_timeout_ms()};")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the Mission Control schema if it does not already exist.

    Idempotent. Safe to call on every connection open.
    """
    conn.executescript(
        """
        -- ── Tier-0 tile state ────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS mission_control_tile_states (
            tile_id TEXT PRIMARY KEY,
            current_state TEXT NOT NULL CHECK (current_state IN ('green','yellow','red','unknown')),
            state_since TEXT NOT NULL,
            last_signature TEXT,
            last_checked_at TEXT NOT NULL,
            last_annotation_at TEXT,
            current_annotation TEXT,
            evidence_payload_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mc_tile_states_color
            ON mission_control_tile_states(current_state);

        -- ── Tier-1 narrative cards ───────────────────────────────────
        CREATE TABLE IF NOT EXISTS mission_control_cards (
            card_id TEXT PRIMARY KEY,
            subject_kind TEXT NOT NULL CHECK (subject_kind IN
                ('task','run','mission','artifact','failure_pattern','infrastructure','idea')),
            subject_id TEXT NOT NULL,
            current_state TEXT NOT NULL CHECK (current_state IN ('live','retired','archived')),
            severity TEXT NOT NULL CHECK (severity IN
                ('critical','warning','watching','informational','success')),
            title TEXT NOT NULL,
            narrative TEXT NOT NULL,
            why_it_matters TEXT NOT NULL,
            recommended_next_step TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            evidence_payload_json TEXT,
            synthesis_history_json TEXT NOT NULL DEFAULT '[]',
            dispatch_history_json TEXT NOT NULL DEFAULT '[]',
            operator_feedback_json TEXT NOT NULL DEFAULT
                '{"thumbs":null,"snoozed_until":null,"comments":[]}',
            last_viewed_at_json TEXT NOT NULL DEFAULT '{}',
            first_observed_at TEXT NOT NULL,
            last_synthesized_at TEXT NOT NULL,
            last_evidence_signature TEXT,
            recurrence_count INTEGER NOT NULL DEFAULT 1,
            synthesis_model TEXT,
            UNIQUE (subject_kind, subject_id)
        );
        CREATE INDEX IF NOT EXISTS idx_mc_cards_state_severity
            ON mission_control_cards(current_state, severity);
        CREATE INDEX IF NOT EXISTS idx_mc_cards_subject
            ON mission_control_cards(subject_kind, subject_id);
        CREATE INDEX IF NOT EXISTS idx_mc_cards_last_synth
            ON mission_control_cards(last_synthesized_at DESC);

        -- ── Action-button audit trail ────────────────────────────────
        CREATE TABLE IF NOT EXISTS mission_control_dispatch_history (
            dispatch_id TEXT PRIMARY KEY,
            card_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN
                ('prompt_generated_for_external','dispatched_to_codie')),
            ts TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            operator_steering_text TEXT,
            task_id TEXT,
            FOREIGN KEY (card_id) REFERENCES mission_control_cards(card_id)
        );
        CREATE INDEX IF NOT EXISTS idx_mc_dispatch_card
            ON mission_control_dispatch_history(card_id, ts DESC);

        -- ── Event-title template cache (Phase 7) ─────────────────────
        CREATE TABLE IF NOT EXISTS event_title_templates (
            template_id TEXT PRIMARY KEY,
            event_kind TEXT NOT NULL,
            metadata_shape_signature TEXT NOT NULL,
            title_template TEXT NOT NULL,
            generated_by_model TEXT,
            generated_at TEXT NOT NULL,
            validated_at TEXT,
            operator_override_text TEXT,
            UNIQUE (event_kind, metadata_shape_signature)
        );
        """
    )


# ── Phase-flag helpers ───────────────────────────────────────────────────
# Each phase gates its runtime side effects behind UA_MC_PHASE_<N>_ENABLED.
# Phase 0 (foundations) is always considered enabled because it's pure
# scaffolding — defining tables and resolvers does nothing until later
# phases turn on the consumers.

_PHASE_FLAG_PREFIX = "UA_MC_PHASE_"


def is_phase_enabled(phase: int) -> bool:
    """Return True if the given Mission Control rollout phase is enabled.

    Phase 0 always returns True (scaffolding only). Phases 1..8 require
    `UA_MC_PHASE_<N>_ENABLED` to be set to a truthy value (1/true/yes/on).
    """
    if phase <= 0:
        return True
    raw = (os.getenv(f"{_PHASE_FLAG_PREFIX}{phase}_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}
