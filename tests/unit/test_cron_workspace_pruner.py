"""cron_* workspace pruner: aged entries in attempts/ + work_products/ are
hard-deleted while the persistent workspace dir, its containers, root
bookkeeping, the newest attempt, and any manifest canonical/latest attempt
always survive. Mirrors tests/unit/test_vp_coder_workspace_pruner.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from universal_agent.scripts.cron_workspace_pruner import (
    _dry_run,
    _prune_workspace,
    _retention_hours,
)


def _age(path: Path, seconds_ago: float) -> None:
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def _make_ws(tmp_path: Path, name: str = "cron_test") -> Path:
    ws = tmp_path / name
    (ws / "attempts").mkdir(parents=True)
    (ws / "work_products").mkdir(parents=True)
    return ws


def test_prunes_aged_attempts_and_work_products(tmp_path):
    ws = _make_ws(tmp_path)
    old_att = ws / "attempts" / "001"
    old_att.mkdir()
    (old_att / "f.txt").write_text("x")
    old_wp = ws / "work_products" / "old.md"
    old_wp.write_text("x")
    fresh_att = ws / "attempts" / "002"
    fresh_att.mkdir()
    (fresh_att / "f.txt").write_text("x")
    fresh_wp = ws / "work_products" / "new.md"
    fresh_wp.write_text("x")
    _age(old_att, 10 * 24 * 3600)
    _age(old_wp, 10 * 24 * 3600)

    stats = _prune_workspace(ws, retention_hours=168, dry_run=False)

    assert not old_att.exists()
    assert not old_wp.exists()
    assert fresh_att.exists()
    assert fresh_wp.exists()
    assert stats["deleted_dirs"] >= 1
    assert stats["deleted_files"] >= 1
    assert stats["freed_bytes"] >= 2  # at least the bytes of the two aged files


def test_preserves_fresh_entries(tmp_path):
    ws = _make_ws(tmp_path)
    fresh = ws / "work_products" / "fresh.md"
    fresh.write_text("x")  # mtime ~ now
    old = ws / "work_products" / "old.md"
    old.write_text("x")
    _age(old, 10 * 24 * 3600)

    _prune_workspace(ws, retention_hours=168, dry_run=False)

    assert fresh.exists()
    assert not old.exists()


def test_preserves_newest_attempt_even_if_all_aged(tmp_path):
    """Age-independent backstop: the single newest attempt dir always survives."""
    ws = _make_ws(tmp_path)
    a1 = ws / "attempts" / "001"
    a1.mkdir()
    (a1 / "f").write_text("x")
    a2 = ws / "attempts" / "002"
    a2.mkdir()
    (a2 / "f").write_text("x")
    a3 = ws / "attempts" / "003"
    a3.mkdir()
    (a3 / "f").write_text("x")
    _age(a1, 20 * 24 * 3600)
    _age(a2, 19 * 24 * 3600)
    _age(a3, 18 * 24 * 3600)  # newest of the aged set

    _prune_workspace(ws, retention_hours=168, dry_run=False)

    assert a3.exists()  # newest -> backstop
    assert not a1.exists()
    assert not a2.exists()


def test_never_deletes_persistent_dir_containers_or_bookkeeping(tmp_path):
    ws = _make_ws(tmp_path)
    (ws / "run_manifest.json").write_text("{}")  # valid (empty) manifest
    for bookkeeping in ("activity.jsonl", "run.log", "MEMORY.md"):
        (ws / bookkeeping).write_text("x")
    for d in ("memory", "downloads"):
        (ws / d).mkdir()
    fresh = ws / "work_products" / "fresh.md"
    fresh.write_text("x")  # newest -> backstop keeps it, freeing the aged one to be pruned
    old = ws / "work_products" / "old.md"
    old.write_text("x")
    _age(old, 10 * 24 * 3600)

    _prune_workspace(ws, retention_hours=168, dry_run=False)

    assert ws.exists(), "persistent cron_* workspace dir must survive"
    assert (ws / "attempts").is_dir() and (ws / "work_products").is_dir(), "containers survive"
    for bookkeeping in ("run_manifest.json", "activity.jsonl", "run.log", "MEMORY.md"):
        assert (ws / bookkeeping).exists(), f"{bookkeeping} must survive"
    assert (ws / "memory").is_dir() and (ws / "downloads").is_dir()
    assert not old.exists(), "aged prunable entry still removed"
    assert fresh.exists()


def test_manifest_canonical_attempt_preserved_when_aged(tmp_path):
    """When run_manifest.json names a canonical/latest attempt, that dir is
    protected independently of the newest-entry backstop."""
    ws = _make_ws(tmp_path)
    for n, days in (("001", 10), ("002", 20), ("003", 30)):
        d = ws / "attempts" / n
        d.mkdir()
        (d / "f").write_text("x")
        _age(d, days * 24 * 3600)  # all aged > 168h; 001 newest of the set
    (ws / "run_manifest.json").write_text(
        json.dumps({"canonical_attempt_number": 2, "latest_attempt_number": 2})
    )

    _prune_workspace(ws, retention_hours=168, dry_run=False)

    assert (ws / "attempts" / "001").exists()  # newest -> backstop
    assert (ws / "attempts" / "002").exists()  # manifest canonical/latest
    assert not (ws / "attempts" / "003").exists()


def test_dry_run_deletes_nothing_but_counts(tmp_path):
    ws = _make_ws(tmp_path)
    fresh = ws / "work_products" / "fresh.md"
    fresh.write_text("x")  # newest -> backstop, so the aged entry is counted as deletable
    old = ws / "work_products" / "old.md"
    old.write_text("x")
    _age(old, 10 * 24 * 3600)

    stats = _prune_workspace(ws, retention_hours=168, dry_run=True)

    assert old.exists(), "dry-run must not delete"
    assert fresh.exists()
    assert stats["deleted_files"] >= 1, "dry-run still counts would-be deletions"
    assert stats["freed_bytes"] >= 1, "dry-run reports would-be-freed bytes"


def test_retention_env_default_invalid_and_override(monkeypatch):
    monkeypatch.delenv("UA_CRON_WORKSPACE_RETENTION_HOURS", raising=False)
    assert _retention_hours() == 168
    monkeypatch.setenv("UA_CRON_WORKSPACE_RETENTION_HOURS", "24")
    assert _retention_hours() == 24
    monkeypatch.setenv("UA_CRON_WORKSPACE_RETENTION_HOURS", "bogus")
    assert _retention_hours() == 168  # invalid -> default
    monkeypatch.setenv("UA_CRON_WORKSPACE_RETENTION_HOURS", "0")
    assert _retention_hours() == 168  # non-positive -> default


def test_dry_run_env(monkeypatch):
    monkeypatch.setenv("UA_CRON_WORKSPACE_PRUNE_DRY_RUN", "1")
    assert _dry_run() is True
    monkeypatch.setenv("UA_CRON_WORKSPACE_PRUNE_DRY_RUN", "false")
    assert _dry_run() is False
    monkeypatch.delenv("UA_CRON_WORKSPACE_PRUNE_DRY_RUN", raising=False)
    assert _dry_run() is False
