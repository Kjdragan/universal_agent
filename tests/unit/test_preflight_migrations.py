"""Tests for scripts/preflight_migrations.py.

Pins two behaviors that protect against the 2026-05-27 deploy crashloop:

1. A pre-tier vp_missions schema (the exact production crash shape)
   makes preflight exit 1 — i.e. the deploy would abort BEFORE the
   service restart that would otherwise crashloop. NOT YET TRUE: the
   migration is fixed now (#504), so even the pre-tier shape passes
   preflight on current code. This test instead verifies the inverse:
   a fixed migration produces ok=True on the pre-tier shape.

2. A non-vp_missions DB is silently skipped rather than treated as
   failure — so adding the preflight doesn't false-fire on workspaces
   that legitimately don't host vp_missions.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest

# Load scripts/preflight_migrations.py as a module — it's not a package
# member so we can't `import` it normally.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "preflight_migrations",
    _REPO_ROOT / "scripts" / "preflight_migrations.py",
)
preflight = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
sys.modules["preflight_migrations"] = preflight
_SPEC.loader.exec_module(preflight)  # type: ignore[union-attr]


def _seed_pre_tier_vp_missions(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE vp_sessions (
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
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE vp_missions (
          mission_id TEXT PRIMARY KEY,
          vp_id TEXT NOT NULL,
          run_id TEXT,
          status TEXT NOT NULL,
          mission_type TEXT,
          objective TEXT NOT NULL,
          priority INTEGER DEFAULT 100,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def test_preflight_passes_on_pre_tier_db_with_current_fixed_migration(tmp_path):
    db = tmp_path / "fake_runtime_state.db"
    _seed_pre_tier_vp_missions(db)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = preflight._run_one(db, scratch)
    assert result["ok"], f"preflight failed unexpectedly: {result['error']}"


def test_preflight_skips_db_without_vp_missions_table(tmp_path):
    db = tmp_path / "unrelated.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = preflight._run_one(db, scratch)
    assert result["ok"]
    assert "skipped" in result["error"]


def test_preflight_reports_missing_file_as_failure(tmp_path):
    db = tmp_path / "does_not_exist.db"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = preflight._run_one(db, scratch)
    assert not result["ok"]
    assert "missing" in result["error"].lower()


def test_preflight_detects_simulated_migration_crash(tmp_path, monkeypatch):
    """If ensure_schema raises, preflight reports ok=False with traceback.

    Simulates the class of failure the script is meant to catch
    (whatever the next migration bug turns out to be).
    """
    db = tmp_path / "fake_runtime_state.db"
    _seed_pre_tier_vp_missions(db)

    def _boom(_conn):
        raise sqlite3.OperationalError("no such column: synthetic_test_only")

    monkeypatch.setattr(preflight, "ensure_schema", _boom)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = preflight._run_one(db, scratch)
    assert not result["ok"]
    assert "OperationalError" in result["error"]
    assert "synthetic_test_only" in result["error"]
