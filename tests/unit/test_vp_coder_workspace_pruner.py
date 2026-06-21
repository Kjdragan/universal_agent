"""H21: VP-coder workspace pruner resolves the writer's path and hard-deletes
aged archive entries (archiving alone — a same-FS move — frees nothing)."""

from __future__ import annotations

import time
from pathlib import Path

from universal_agent.scripts.vp_coder_workspace_pruner import (
    _hard_delete_aged_archive,
    _resolve_coder_workspace_root,
)


def test_resolve_root_matches_writer_default(monkeypatch, tmp_path):
    # With UA_VP_CODER_WORKSPACE_ROOT unset, the pruner must resolve the SAME
    # fallback the writer uses (…/vp_coder_primary_external), not no-op.
    monkeypatch.delenv("UA_VP_CODER_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("UA_VP_ENABLED_IDS", "vp.coder.primary")
    root = _resolve_coder_workspace_root()
    assert root is not None
    assert root.name == "vp_coder_primary_external"


def test_hard_delete_removes_only_aged_dirs(tmp_path):
    archive = tmp_path / "vp_coder_primary_external_archive"
    archive.mkdir()
    old = archive / "vp-mission-old"
    new = archive / "vp-mission-new"
    old.mkdir()
    new.mkdir()
    (old / "f.txt").write_text("x")
    (new / "f.txt").write_text("x")
    # Age `old` to 20 days; `new` stays fresh.
    twenty_days_ago = time.time() - 20 * 24 * 3600
    import os
    os.utime(old, (twenty_days_ago, twenty_days_ago))

    deleted = _hard_delete_aged_archive(archive, max_age_hours=14 * 24)  # 14-day grace
    assert deleted == 1
    assert not old.exists()
    assert new.exists()


def test_hard_delete_noop_when_archive_absent(tmp_path):
    assert _hard_delete_aged_archive(tmp_path / "nope", max_age_hours=336) == 0
