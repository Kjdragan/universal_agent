"""P5 guards (15_demo_tutorial_pipeline_adr.md): Tutorial/Demo -> build-session link.

Pins: _session_viewer_url shapes; _list_tutorial_runs + _claude_code_intel_demos
passthrough (and pre-P5 backward compat: missing ids -> empty strings, no link,
never an error); both manifest stampers merge without clobbering.
"""

from __future__ import annotations

import json
from pathlib import Path

from universal_agent import gateway_server, hooks_service
from universal_agent.vp import worker_loop


def test_session_viewer_url_prefers_session_and_forwards_workspace():
    url = gateway_server._session_viewer_url(
        session_id="vp-mission-abc",
        run_id="run-x",
        workspace_dir="/opt/ua_demos/foo__demo-1",
    )
    assert url.startswith("/?")
    assert "session_id=vp-mission-abc" in url
    assert "run_id" not in url  # session wins; never both (openViewer contract)
    assert "workspace=%2Fopt%2Fua_demos%2Ffoo__demo-1" in url
    assert "role=viewer" in url


def test_session_viewer_url_run_fallback_and_no_identity():
    assert "run_id=run-1" in gateway_server._session_viewer_url(run_id="run-1")
    assert gateway_server._session_viewer_url(workspace_dir="/tmp/x") == ""
    assert gateway_server._session_viewer_url() == ""


def test_list_tutorial_runs_passthrough_and_backward_compat(tmp_path, monkeypatch):
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", artifacts_root)
    root = artifacts_root / "youtube-tutorial-creation"

    stamped = root / "vid1__run"
    stamped.mkdir(parents=True)
    (stamped / "manifest.json").write_text(
        json.dumps(
            {
                "video_id": "vid1",
                "title": "Stamped",
                "status": "full",
                "build_session_id": "session_hook_yt_chan__vid1",
                "build_run_id": "wfrun-1",
                "build_workspace_dir": "/srv/AGENT_RUN_WORKSPACES/run_session_hook_yt_chan__vid1",
            }
        ),
        encoding="utf-8",
    )
    legacy = root / "vid2__run"
    legacy.mkdir(parents=True)
    (legacy / "manifest.json").write_text(
        json.dumps({"video_id": "vid2", "title": "Legacy", "status": "full"}),
        encoding="utf-8",
    )

    runs = {r["video_id"]: r for r in gateway_server._list_tutorial_runs(limit=10)}
    stamped_run = runs["vid1"]
    assert stamped_run["session_id"] == "session_hook_yt_chan__vid1"
    assert stamped_run["run_id"] == "wfrun-1"
    assert "session_id=session_hook_yt_chan__vid1" in stamped_run["session_url"]
    assert "workspace=" in stamped_run["session_url"]

    legacy_run = runs["vid2"]
    assert legacy_run["session_id"] == ""
    assert legacy_run["run_id"] == ""
    assert legacy_run["session_url"] == ""


def test_demos_walker_emits_session_link_fields(tmp_path):
    demos_root = tmp_path / "ua_demos"
    stamped = demos_root / "feature__demo-1"
    stamped.mkdir(parents=True)
    (stamped / "manifest.json").write_text(
        json.dumps(
            {
                "feature": "feature",
                "endpoint_hit": "zai",
                "build_mission_id": "vp-mission-deadbeef",
                "build_session_id": "vp-mission-deadbeef",
            }
        ),
        encoding="utf-8",
    )
    legacy = demos_root / "old__demo-2"
    legacy.mkdir(parents=True)
    (legacy / "manifest.json").write_text(json.dumps({"feature": "old"}), encoding="utf-8")

    result = gateway_server._claude_code_intel_demos(
        demos_root=demos_root, vault_root=tmp_path / "vault"
    )
    by_id = {d["demo_id"]: d for d in result}
    new = by_id["feature__demo-1"]
    assert new["session_id"] == "vp-mission-deadbeef"
    assert new["run_id"] == "vp-mission-deadbeef"
    assert "session_id=vp-mission-deadbeef" in new["session_url"]
    assert "workspace=" in new["session_url"]

    old = by_id["old__demo-2"]
    assert old["session_id"] == ""
    assert old["run_id"] == ""
    assert old["session_url"] == ""


def test_stamp_demo_manifest_merges_and_preserves(tmp_path):
    ws = tmp_path / "demo"
    ws.mkdir()
    (ws / "manifest.json").write_text(
        json.dumps({"demo_id": "demo", "endpoint_hit": "zai"}), encoding="utf-8"
    )
    ok = worker_loop._stamp_demo_manifest_build_session(
        workspace_dir=str(ws),
        mission_id="vp-mission-123",
        vp_id="vp.coder.primary",
        cody_session_id="cli-uuid-1",
    )
    assert ok is True
    payload = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert payload["endpoint_hit"] == "zai"  # existing fields preserved
    assert payload["build_mission_id"] == "vp-mission-123"
    assert payload["build_session_id"] == "vp-mission-123"
    assert payload["build_vp_id"] == "vp.coder.primary"
    assert payload["build_cli_session_id"] == "cli-uuid-1"


def test_stamp_demo_manifest_vp_mission_subdir_layout(tmp_path):
    ws = tmp_path / "demo"
    sub = ws / "vp-mission-abc"
    sub.mkdir(parents=True)
    (sub / "manifest.json").write_text(json.dumps({"demo_id": "demo"}), encoding="utf-8")
    assert worker_loop._stamp_demo_manifest_build_session(
        workspace_dir=str(ws), mission_id="vp-mission-abc", vp_id="vp.coder.primary"
    ) is True
    payload = json.loads((sub / "manifest.json").read_text(encoding="utf-8"))
    assert payload["build_mission_id"] == "vp-mission-abc"


def test_stamp_demo_manifest_missing_manifest_is_noop(tmp_path):
    assert worker_loop._stamp_demo_manifest_build_session(
        workspace_dir=str(tmp_path), mission_id="vp-mission-1", vp_id="vp.coder.primary"
    ) is False
    assert worker_loop._stamp_demo_manifest_build_session(
        workspace_dir=str(tmp_path / "missing"),
        mission_id="vp-mission-1",
        vp_id="vp.coder.primary",
    ) is False


def test_stamp_tutorial_manifest_merges_session_identity(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = run_dir / "manifest.json"
    manifest.write_text(json.dumps({"video_id": "vid1", "status": "full"}), encoding="utf-8")
    hooks_service._stamp_tutorial_manifest_build_session(
        {"manifest_path": str(manifest)},
        session_id="session_hook_yt_chan__vid1",
        run_id="wfrun-9",
        workspace_dir="/srv/ws",
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["video_id"] == "vid1"  # existing fields preserved
    assert payload["build_session_id"] == "session_hook_yt_chan__vid1"
    assert payload["build_run_id"] == "wfrun-9"
    assert payload["build_workspace_dir"] == "/srv/ws"


def test_stamp_tutorial_manifest_never_raises_on_missing(tmp_path):
    hooks_service._stamp_tutorial_manifest_build_session(
        {"manifest_path": str(tmp_path / "nope.json")},
        session_id="sid",
        run_id=None,
        workspace_dir="",
    )  # must not raise
