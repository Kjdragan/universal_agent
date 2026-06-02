"""Unit tests for the tailnet-scratchpad retention sweep."""

from __future__ import annotations

import os
import time

import pytest

from universal_agent.scripts import prune_scratch


@pytest.fixture
def scratch_root(tmp_path):
    # A dedicated root so assertions on removed/kept counts aren't polluted by
    # anything the shared conftest seeds into tmp_path (e.g. ua_test_dbs/).
    root = tmp_path / "ua_scratch"
    root.mkdir()
    return root


def _make_slug(root, name, *, age_days: float, now: float):
    d = root / name
    d.mkdir()
    f = d / "report.html"
    f.write_text("<html></html>", encoding="utf-8")
    ts = now - age_days * 86400
    os.utime(f, (ts, ts))
    os.utime(d, (ts, ts))
    return d


def test_removes_old_keeps_fresh(scratch_root):
    now = time.time()
    old = _make_slug(scratch_root, "old-1", age_days=45, now=now)
    fresh = _make_slug(scratch_root, "fresh-1", age_days=5, now=now)

    result = prune_scratch.prune_scratch(root=scratch_root, retention_days=30, now=now)

    assert not old.exists(), "a 45-day-old slug-dir must be pruned at 30-day retention"
    assert fresh.exists(), "a 5-day-old slug-dir must be kept"
    assert result["removed"] == 1
    assert result["kept"] == 1


def test_boundary(scratch_root):
    now = time.time()
    # Just inside the window (29.9d) is kept; just past it (30.1d) is removed.
    keep = _make_slug(scratch_root, "edge-keep", age_days=29.9, now=now)
    drop = _make_slug(scratch_root, "edge-drop", age_days=30.1, now=now)

    prune_scratch.prune_scratch(root=scratch_root, retention_days=30, now=now)

    assert keep.exists()
    assert not drop.exists()


def test_recent_file_keeps_old_dir(scratch_root):
    now = time.time()
    d = scratch_root / "republished"
    d.mkdir()
    old_ts = now - 60 * 86400
    os.utime(d, (old_ts, old_ts))
    # A freshly written file inside an old dir makes the dir count as fresh.
    (d / "report.html").write_text("<html></html>", encoding="utf-8")  # mtime = now

    prune_scratch.prune_scratch(root=scratch_root, retention_days=30, now=now)

    assert d.exists(), "dir with a recently (re)published file must be kept"


def test_missing_root_is_noop(tmp_path):
    result = prune_scratch.prune_scratch(root=tmp_path / "nope", retention_days=30)
    assert result["removed"] == 0 and result["kept"] == 0 and result["errors"] == 0


def test_retention_days_from_env(monkeypatch):
    monkeypatch.setenv("UA_SCRATCH_RETENTION_DAYS", "7")
    assert prune_scratch._retention_days() == 7
    monkeypatch.setenv("UA_SCRATCH_RETENTION_DAYS", "garbage")
    assert prune_scratch._retention_days() == prune_scratch.DEFAULT_RETENTION_DAYS
    monkeypatch.delenv("UA_SCRATCH_RETENTION_DAYS", raising=False)
    assert prune_scratch._retention_days() == prune_scratch.DEFAULT_RETENTION_DAYS
