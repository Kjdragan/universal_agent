"""Tests for the parallel-vault backfill (PR 12)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from universal_agent.services import backfill_v2
from universal_agent.services.backfill_v2 import (
    ARCHIVE_SUFFIX,
    DEFAULT_PARALLEL_SUFFIX,
    VAULT_PATH_OVERRIDE_ENV,
    BackfillStats,
    PacketReplayRecord,
    SwapResult,
    archive_vault_root,
    canonical_vault_root,
    compute_vault_diff,
    enumerate_packets,
    packets_root_for,
    parallel_vault_root,
    revert_swap,
    run_backfill,
    swap_vaults,
)


# ── Path helpers ────────────────────────────────────────────────────────────


def test_path_helpers_use_artifacts_root(tmp_path: Path):
    # packets_root layout is <artifacts>/proactive/<LANE_SLUG>/packets
    assert packets_root_for(tmp_path).parent.parent.parent == tmp_path
    assert canonical_vault_root(tmp_path).parent == tmp_path / "knowledge-vaults"
    assert parallel_vault_root(tmp_path).name.endswith(DEFAULT_PARALLEL_SUFFIX)
    assert archive_vault_root(tmp_path).name.endswith(ARCHIVE_SUFFIX)


def test_parallel_and_canonical_are_distinct(tmp_path: Path):
    assert canonical_vault_root(tmp_path) != parallel_vault_root(tmp_path)


# ── enumerate_packets ───────────────────────────────────────────────────────


def _make_packet(root: Path, *, date: str, stamp: str, handle: str = "ClaudeDevs") -> Path:
    target = root / date / f"{stamp}__{handle}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text("{}", encoding="utf-8")
    return target


def test_enumerate_packets_returns_chronological(tmp_path: Path):
    root = tmp_path / "packets"
    _make_packet(root, date="2026-04-01", stamp="120000")
    _make_packet(root, date="2026-03-15", stamp="160000")
    _make_packet(root, date="2026-04-01", stamp="100000")
    packets = enumerate_packets(root)
    # Sorted by (date_dir, packet_dir).
    names = [p.name for p in packets]
    dates = [p.parent.name for p in packets]
    assert dates == ["2026-03-15", "2026-04-01", "2026-04-01"]
    # Within the same date, by HHMMSS prefix in dir name.
    assert names[1] < names[2]


def test_enumerate_packets_skips_missing_manifest(tmp_path: Path):
    root = tmp_path / "packets"
    valid = _make_packet(root, date="2026-04-01", stamp="120000")
    no_manifest = root / "2026-04-02" / "120000__ClaudeDevs"
    no_manifest.mkdir(parents=True)
    # No manifest.json written.
    packets = enumerate_packets(root)
    assert valid in packets
    assert no_manifest not in packets


def test_enumerate_packets_returns_empty_for_missing_root(tmp_path: Path):
    assert enumerate_packets(tmp_path / "nope") == []


def test_enumerate_packets_skips_files_at_top_level(tmp_path: Path):
    root = tmp_path / "packets"
    root.mkdir()
    (root / "stray_file.txt").write_text("x", encoding="utf-8")
    _make_packet(root, date="2026-04-01", stamp="120000")
    packets = enumerate_packets(root)
    assert len(packets) == 1


# ── run_backfill (with replay_packet stubbed) ───────────────────────────────


def test_run_backfill_invokes_replay_per_packet(monkeypatch, tmp_path: Path):
    src_packets = tmp_path / "packets"
    parallel = tmp_path / "vault-v2"
    p1 = _make_packet(src_packets, date="2026-04-01", stamp="120000")
    p2 = _make_packet(src_packets, date="2026-04-02", stamp="130000")

    captured: list[str] = []

    class FakeConfig:
        def __init__(self, **kw):
            self.kw = kw

    def stub_replay(*, config, conn=None):
        captured.append(str(config.kw.get("packet_dir") or ""))
        # Stub return shape mirrors real replay_packet's relevant fields.
        return {
            "ok": True,
            "new_post_count": 2,
            "action_count": 1,
            "memex_actions": [{"action": "CREATE"}],
            "grounded_source_count": 0,
        }

    # Patch the symbols replay_packet is imported under inside backfill_v2.
    import universal_agent.services.claude_code_intel_replay as replay_mod
    monkeypatch.setattr(replay_mod, "replay_packet", stub_replay)
    monkeypatch.setattr(replay_mod, "ClaudeCodeIntelReplayConfig", FakeConfig)

    stats = run_backfill(
        packets_root=src_packets,
        parallel_vault=parallel,
        artifacts_root=tmp_path,
        queue_task_hub=False,
    )
    assert stats.packets_total == 2
    assert stats.packets_replayed_ok == 2
    assert stats.packets_failed == 0
    assert str(p1) in captured
    assert str(p2) in captured


def test_run_backfill_sets_and_restores_env_var(monkeypatch, tmp_path: Path):
    src_packets = tmp_path / "packets"
    parallel = tmp_path / "vault-v2"
    _make_packet(src_packets, date="2026-04-01", stamp="120000")

    monkeypatch.delenv(VAULT_PATH_OVERRIDE_ENV, raising=False)

    seen_during_replay: dict[str, str | None] = {}

    class FakeConfig:
        def __init__(self, **kw):
            pass

    def stub_replay(*, config, conn=None):
        seen_during_replay["env"] = os.environ.get(VAULT_PATH_OVERRIDE_ENV)
        return {"ok": True}

    import universal_agent.services.claude_code_intel_replay as replay_mod
    monkeypatch.setattr(replay_mod, "replay_packet", stub_replay)
    monkeypatch.setattr(replay_mod, "ClaudeCodeIntelReplayConfig", FakeConfig)

    run_backfill(packets_root=src_packets, parallel_vault=parallel, artifacts_root=tmp_path)

    # Inside replay, the env var was set.
    assert seen_during_replay["env"] == str(parallel)
    # After backfill returns, the env var is unset (restored).
    assert os.environ.get(VAULT_PATH_OVERRIDE_ENV) is None


def test_run_backfill_restores_prior_env_var(monkeypatch, tmp_path: Path):
    src_packets = tmp_path / "packets"
    parallel = tmp_path / "vault-v2"
    _make_packet(src_packets, date="2026-04-01", stamp="120000")
    monkeypatch.setenv(VAULT_PATH_OVERRIDE_ENV, "/some/preexisting/path")

    class FakeConfig:
        def __init__(self, **kw):
            pass

    def stub_replay(*, config, conn=None):
        return {"ok": True}

    import universal_agent.services.claude_code_intel_replay as replay_mod
    monkeypatch.setattr(replay_mod, "replay_packet", stub_replay)
    monkeypatch.setattr(replay_mod, "ClaudeCodeIntelReplayConfig", FakeConfig)

    run_backfill(packets_root=src_packets, parallel_vault=parallel, artifacts_root=tmp_path)
    # Restored to the prior value.
    assert os.environ.get(VAULT_PATH_OVERRIDE_ENV) == "/some/preexisting/path"


def test_run_backfill_per_packet_failure_is_isolated(monkeypatch, tmp_path: Path):
    src_packets = tmp_path / "packets"
    parallel = tmp_path / "vault-v2"
    _make_packet(src_packets, date="2026-04-01", stamp="120000")
    _make_packet(src_packets, date="2026-04-02", stamp="130000")

    call_count = {"n": 0}

    class FakeConfig:
        def __init__(self, **kw):
            pass

    def stub_replay(*, config, conn=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated_first_replay_failure")
        return {"ok": True, "new_post_count": 1, "action_count": 1}

    import universal_agent.services.claude_code_intel_replay as replay_mod
    monkeypatch.setattr(replay_mod, "replay_packet", stub_replay)
    monkeypatch.setattr(replay_mod, "ClaudeCodeIntelReplayConfig", FakeConfig)

    stats = run_backfill(packets_root=src_packets, parallel_vault=parallel, artifacts_root=tmp_path)
    assert stats.packets_total == 2
    assert stats.packets_failed == 1
    assert stats.packets_replayed_ok == 1
    failed_records = [r for r in stats.records if not r.ok]
    assert any("simulated_first_replay_failure" in r.error for r in failed_records)


def test_run_backfill_stop_on_error_halts(monkeypatch, tmp_path: Path):
    src_packets = tmp_path / "packets"
    parallel = tmp_path / "vault-v2"
    _make_packet(src_packets, date="2026-04-01", stamp="120000")
    _make_packet(src_packets, date="2026-04-02", stamp="130000")

    call_count = {"n": 0}

    class FakeConfig:
        def __init__(self, **kw):
            pass

    def stub_replay(*, config, conn=None):
        call_count["n"] += 1
        raise RuntimeError("simulated_failure")

    import universal_agent.services.claude_code_intel_replay as replay_mod
    monkeypatch.setattr(replay_mod, "replay_packet", stub_replay)
    monkeypatch.setattr(replay_mod, "ClaudeCodeIntelReplayConfig", FakeConfig)

    stats = run_backfill(
        packets_root=src_packets,
        parallel_vault=parallel,
        artifacts_root=tmp_path,
        stop_on_error=True,
    )
    # Only the first packet attempted because stop_on_error halted the loop.
    assert call_count["n"] == 1
    assert stats.packets_failed == 1


# ── compute_vault_diff ──────────────────────────────────────────────────────


def test_compute_diff_counts_files_in_each_subtree(tmp_path: Path):
    canonical = tmp_path / "canonical"
    parallel = tmp_path / "parallel"
    (canonical / "entities").mkdir(parents=True)
    (canonical / "entities" / "skills.md").write_text("# Skills\n", encoding="utf-8")
    (canonical / "sources").mkdir()
    (canonical / "sources" / "src1.md").write_text("# S\n", encoding="utf-8")
    (parallel / "entities").mkdir(parents=True)
    (parallel / "entities" / "skills.md").write_text("# Skills\n", encoding="utf-8")
    (parallel / "entities" / "memory_tool.md").write_text("# MT\n", encoding="utf-8")

    diff = compute_vault_diff(canonical, parallel)
    assert diff["canonical_exists"] is True
    assert diff["parallel_exists"] is True
    assert diff["canonical"]["entities"] == 1
    assert diff["canonical"]["sources"] == 1
    assert diff["parallel"]["entities"] == 2


def test_compute_diff_handles_missing_paths(tmp_path: Path):
    diff = compute_vault_diff(tmp_path / "nope1", tmp_path / "nope2")
    assert diff["canonical_exists"] is False
    assert diff["parallel_exists"] is False
    assert diff["canonical"]["total_md"] == 0
    assert diff["parallel"]["total_md"] == 0


# ── swap_vaults ─────────────────────────────────────────────────────────────


def test_swap_vaults_renames_canonical_and_parallel(tmp_path: Path):
    canonical = tmp_path / "vault"
    parallel = tmp_path / "vault-v2"
    canonical.mkdir()
    parallel.mkdir()
    (canonical / "marker.txt").write_text("old", encoding="utf-8")
    (parallel / "marker.txt").write_text("new", encoding="utf-8")

    result = swap_vaults(canonical=canonical, parallel=parallel)
    assert result.swapped is True
    # canonical dir now has the new content.
    assert (canonical / "marker.txt").read_text(encoding="utf-8") == "new"
    # archive dir has the old content.
    archive = canonical.with_name(canonical.name + ARCHIVE_SUFFIX)
    assert archive.exists()
    assert (archive / "marker.txt").read_text(encoding="utf-8") == "old"
    # parallel is gone after swap.
    assert not parallel.exists()


def test_swap_refuses_when_parallel_missing(tmp_path: Path):
    canonical = tmp_path / "vault"
    parallel = tmp_path / "vault-v2"
    canonical.mkdir()
    result = swap_vaults(canonical=canonical, parallel=parallel)
    assert result.swapped is False
    assert "parallel_missing" in result.skipped_reason


def test_swap_refuses_when_archive_exists(tmp_path: Path):
    canonical = tmp_path / "vault"
    parallel = tmp_path / "vault-v2"
    archive = canonical.with_name(canonical.name + ARCHIVE_SUFFIX)
    canonical.mkdir()
    parallel.mkdir()
    archive.mkdir()
    result = swap_vaults(canonical=canonical, parallel=parallel)
    assert result.swapped is False
    assert "archive_exists" in result.skipped_reason
    # canonical and parallel still exist (no partial state).
    assert canonical.exists()
    assert parallel.exists()


def test_swap_overwrite_archive_replaces_old_archive(tmp_path: Path):
    canonical = tmp_path / "vault"
    parallel = tmp_path / "vault-v2"
    archive = canonical.with_name(canonical.name + ARCHIVE_SUFFIX)
    canonical.mkdir()
    parallel.mkdir()
    archive.mkdir()
    (archive / "old_archive_marker.txt").write_text("OLD ARCHIVE", encoding="utf-8")
    (canonical / "marker.txt").write_text("about_to_be_archived", encoding="utf-8")

    result = swap_vaults(canonical=canonical, parallel=parallel, overwrite_archive=True)
    assert result.swapped is True
    # Old archive content is gone — it was overwritten by the freshly-archived canonical.
    assert not (archive / "old_archive_marker.txt").exists()
    assert (archive / "marker.txt").read_text(encoding="utf-8") == "about_to_be_archived"


def test_swap_when_canonical_missing_only_parallel_renames(tmp_path: Path):
    """First-ever swap when there's no v1 vault yet — parallel just becomes canonical."""
    canonical = tmp_path / "vault"
    parallel = tmp_path / "vault-v2"
    parallel.mkdir()
    (parallel / "marker.txt").write_text("new", encoding="utf-8")
    result = swap_vaults(canonical=canonical, parallel=parallel)
    assert result.swapped is True
    assert (canonical / "marker.txt").read_text(encoding="utf-8") == "new"


# ── revert_swap ─────────────────────────────────────────────────────────────


def test_revert_swap_restores_archive_to_canonical(tmp_path: Path):
    canonical = tmp_path / "vault"
    archive = canonical.with_name(canonical.name + ARCHIVE_SUFFIX)
    canonical.mkdir()
    archive.mkdir()
    (canonical / "marker.txt").write_text("bad_new_vault", encoding="utf-8")
    (archive / "marker.txt").write_text("good_old_vault", encoding="utf-8")

    result = revert_swap(canonical=canonical)
    assert result.swapped is True
    # Canonical now has the old (good) content.
    assert (canonical / "marker.txt").read_text(encoding="utf-8") == "good_old_vault"
    # The previously-canonical bad vault is parked elsewhere.
    rolledback = canonical.with_name(canonical.name + "-rolledback")
    assert rolledback.exists()
    assert (rolledback / "marker.txt").read_text(encoding="utf-8") == "bad_new_vault"
    # Archive is consumed.
    assert not archive.exists()


def test_revert_swap_refuses_when_archive_missing(tmp_path: Path):
    canonical = tmp_path / "vault"
    canonical.mkdir()
    result = revert_swap(canonical=canonical)
    assert result.swapped is False
    assert "archive_missing" in result.skipped_reason
