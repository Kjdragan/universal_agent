"""Unit tests for viewer.resolver.mission_log_rel.

Cody/VP CLI missions write run.log into a doubly-nested
``vp_<vp>_external/<mission_id>/<mission_id>/`` dir under AGENT_RUN_WORKSPACES —
NOT into the demo workspace_dir. The viewer's Activity panel needs this path to
rehydrate a demo run. mission_log_rel locates it (globbing so the vp prefix
isn't hard-coded) and returns "" gracefully when absent.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.viewer.resolver import mission_log_rel


def _seed_mission_logs(root: Path, vp: str, mission_id: str) -> Path:
    d = root / f"vp_{vp}_external" / mission_id / mission_id
    d.mkdir(parents=True)
    (d / "run.log").write_text("[00:00:00] USER: hi\n", encoding="utf-8")
    (d / "cli_stream.log").write_text("{}\n", encoding="utf-8")
    return d


def test_mission_log_rel_finds_doubly_nested_run_log(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    mid = "vp-mission-8692bb058e6d648b25566463"
    _seed_mission_logs(tmp_path, "coder_primary", mid)
    rel = mission_log_rel(mid)
    assert rel == f"vp_coder_primary_external/{mid}/{mid}"


def test_mission_log_rel_discovers_vp_prefix_via_glob(tmp_path, monkeypatch):
    # The vp prefix is not hard-coded — a general-VP mission resolves too.
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    mid = "vp-mission-deadbeef"
    _seed_mission_logs(tmp_path, "general_primary", mid)
    assert mission_log_rel(mid) == f"vp_general_primary_external/{mid}/{mid}"


def test_mission_log_rel_empty_when_no_run_log(tmp_path, monkeypatch):
    # Older flat-layout missions (only mission_receipt.json at the root) → "".
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    mid = "vp-mission-flatlayout"
    flat = tmp_path / "vp_coder_primary_external" / mid
    flat.mkdir(parents=True)
    (flat / "mission_receipt.json").write_text("{}", encoding="utf-8")
    assert mission_log_rel(mid) == ""


def test_mission_log_rel_empty_for_non_mission_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    assert mission_log_rel("daemon_simone_heartbeat") == ""
    assert mission_log_rel("") == ""
    assert mission_log_rel(None) == ""
