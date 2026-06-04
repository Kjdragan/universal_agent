"""Deploy preflight: validate schema migrations against a copy of prod DBs.

Catches the class of failure that wedged production at 2026-05-27 16:32:
the `vp_missions.priority_tier` ALTER ordering bug. ``ensure_schema``
crashed during gateway lifespan because ``executescript(SCHEMA_SQL)``
referenced a column that hadn't been added yet. Existing
``verify_service_imports.py`` only checks module imports — it never
calls ``ensure_schema``, so any migration-shaped bug slips through to
service restart and crashloops production.

This script:

1. Locates every DB the gateway+VP services touch.
2. Copies each to a writable temp directory.
3. Calls ``ensure_schema`` against the copy.
4. Reports per-DB result.
5. Exits 1 if **any** DB fails the migration.

It runs on the deploy host (the VPS), against the actual production
schemas, just before service restart. If it fails, deploy aborts and
the operator gets a structured error message instead of an 8-minute
gateway crashloop.

Usage (called from scripts/deploy_validate_runtime.sh):

    PYTHONPATH=src ./.venv/bin/python scripts/preflight_migrations.py
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import traceback

from universal_agent.durable.migrations import ensure_schema


def _candidate_db_paths() -> list[Path]:
    """Return the DB paths the running services rely on for vp_missions etc.

    Hard-coded against the production layout. We deliberately don't read
    these from env (UA_RUNTIME_PATH etc.) because a missing env var
    would silently skip the check; explicit paths fail loudly if the
    layout ever changes.
    """
    base = Path("/opt/universal_agent/AGENT_RUN_WORKSPACES")
    return [
        base / "runtime_state.db",   # gateway lifespan
        base / "vp_state.db",        # VP worker missions
        base / "coder_vp_state.db",  # Cody VP worker
    ]


def _has_vp_missions(db_path: Path) -> bool:
    """Skip DBs that don't host vp_missions (don't need the new migration)."""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='vp_missions' LIMIT 1"
            ).fetchone()
            return row is not None
    except sqlite3.DatabaseError:
        return False


def _run_one(db_path: Path, scratch_dir: Path) -> dict:
    """Copy db_path into scratch_dir and run ensure_schema against the copy.

    Returns a result dict: {path, ok, error}. ``ok`` is False on any
    raise from ensure_schema or pre-flight file ops.
    """
    result: dict = {"path": str(db_path), "ok": False, "error": ""}
    if not db_path.exists():
        result["error"] = "db file missing"
        return result
    if not _has_vp_missions(db_path):
        result["ok"] = True
        result["error"] = "no vp_missions table (skipped)"
        return result
    copy = scratch_dir / db_path.name
    try:
        # Copy under the lock to avoid catching mid-transaction writes.
        # ``backup`` is the SQLite-safe way to snapshot a live DB.
        with sqlite3.connect(str(db_path)) as src, sqlite3.connect(str(copy)) as dst:
            src.backup(dst)
    except Exception as exc:
        result["error"] = f"copy failed: {exc}"
        return result
    try:
        with sqlite3.connect(str(copy)) as conn:
            ensure_schema(conn)
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}\n" + traceback.format_exc()
    return result


def main() -> int:
    print("--> Preflight: validating ensure_schema against snapshots of prod DBs...")
    scratch = Path(tempfile.mkdtemp(prefix="ua-preflight-"))
    results: list[dict] = []
    try:
        for db in _candidate_db_paths():
            r = _run_one(db, scratch)
            results.append(r)
            tag = "OK" if r["ok"] else "FAIL"
            detail = r["error"].splitlines()[0] if r["error"] else ""
            print(f"    [{tag}] {db.name}  {detail}")
    finally:
        # Always clean scratch so /tmp doesn't fill across deploys.
        shutil.rmtree(scratch, ignore_errors=True)

    failed = [r for r in results if not r["ok"]]
    summary = {
        "ok": not failed,
        "checked": len(results),
        "failed": len(failed),
        "results": results,
    }
    print(json.dumps(summary, indent=2))
    if failed:
        print(
            "::error::Schema preflight failed for "
            f"{len(failed)}/{len(results)} DB(s). "
            "Refusing to restart services with a broken migration.",
            file=sys.stderr,
        )
        return 1
    print("--> Preflight OK; all migrations are safe to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
