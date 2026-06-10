"""P6 guards: deterministic tutorial_build demo finalize.

Pins ``services/tutorial_demo_finalize.finalize_tutorial_build_demo``:
manifest synthesis (DemoManifest-compatible, never clobbers a Cody-authored
one), existence-only mechanical checks, UA_DEMOS_ROOT symlink registration,
and the downstream P5 plumbing (the previously no-op'ing
``worker_loop._stamp_demo_manifest_build_session`` now succeeds, and the
``gateway_server._claude_code_intel_demos`` walker surfaces the demo with a
session link — complements
test_demo_tutorial_session_link.py::test_stamp_demo_manifest_missing_manifest_is_noop
and ::test_demos_walker_emits_session_link_fields).
"""

from __future__ import annotations

import json
from pathlib import Path

from universal_agent.services.cody_implementation import read_manifest
from universal_agent.services.tutorial_demo_finalize import (
    _mechanical_checks,
    finalize_tutorial_build_demo,
)


def _task_meta() -> dict:
    return {
        "video_id": "vidP6demo",
        "video_title": "Build an Agent SDK demo",
        "video_url": "https://youtube.test/watch?v=vidP6demo",
    }


def _mission(cody_mode: str = "zai") -> dict:
    return {
        "objective": "Build the tutorial demo",
        "started_at": "2026-06-10T12:00:00+00:00",
        "payload_json": json.dumps({"metadata": {"cody_mode": cody_mode}}),
    }


def _finalize(ws: Path, *, mission_id: str = "vp-mission-p6demo") -> dict:
    return finalize_tutorial_build_demo(
        task_id="tutorial-build:p6",
        task_meta=_task_meta(),
        mission=_mission(),
        mission_id=mission_id,
        workspace_candidates=[str(ws)],
    )


# ── 8. Manifest synthesis ───────────────────────────────────────────────────

def test_finalize_synthesizes_demomanifest_compatible_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    ws = tmp_path / "ws"
    ws.mkdir()

    result = _finalize(ws)
    assert result["ok"] is True

    manifest = read_manifest(ws)
    assert manifest is not None
    assert manifest.endpoint_required == "any"
    assert manifest.endpoint_hit == "zai"
    assert manifest.acceptance_passed is True
    assert manifest.iteration == 1
    assert manifest.started_at == "2026-06-10T12:00:00+00:00"
    assert "synthesized" in manifest.notes

    raw = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert raw["video_id"] == "vidP6demo"
    assert raw["video_title"] == "Build an Agent SDK demo"
    assert raw["timestamp"]
    assert raw["manifest_synthesized"] is True
    assert raw["build_kind"] == "tutorial_build"


def test_finalize_without_workspace_reports_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    result = finalize_tutorial_build_demo(
        task_id="tutorial-build:p6",
        task_meta=_task_meta(),
        mission=_mission(),
        mission_id="vp-mission-p6demo",
        workspace_candidates=["", str(tmp_path / "missing")],
    )
    assert result["ok"] is False
    assert result["reason"] == "no_workspace_dir"


# ── 9. Never clobbers a Cody-authored manifest ──────────────────────────────

def test_finalize_never_clobbers_cody_authored_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    ws = tmp_path / "ws"
    ws.mkdir()
    authored = {
        "demo_id": "cody-authored__demo-1",
        "feature": "authored by cody",
        "endpoint_required": "zai",
        "endpoint_hit": "zai",
        "acceptance_passed": False,
        "iteration": 3,
        "notes": "cody wrote this",
    }
    (ws / "manifest.json").write_text(json.dumps(authored), encoding="utf-8")

    result = _finalize(ws)
    assert result["ok"] is True

    raw = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    # Cody's fields untouched; no synthesis marker.
    assert raw["demo_id"] == "cody-authored__demo-1"
    assert raw["feature"] == "authored by cody"
    assert raw["iteration"] == 3
    assert raw["notes"] == "cody wrote this"
    assert "manifest_synthesized" not in raw
    # Finalize's only write is the additive mechanical_checks merge.
    assert "mechanical_checks" in raw


# ── 10. Mechanical checks ───────────────────────────────────────────────────

def test_mechanical_checks_pass_with_pyproject_and_run_readme(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (ws / "README.md").write_text(
        "# Demo\n\n## Run\n\n```\nuv run python main.py\n```\n", encoding="utf-8"
    )

    checks = _mechanical_checks(ws)
    assert checks == {"venv_or_project": True, "readme_run_instructions": True}

    result = _finalize(ws)
    assert result["checks"]["venv_or_project"] is True
    assert result["checks"]["readme_run_instructions"] is True
    # Checks persisted into the manifest.
    raw = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert raw["mechanical_checks"]["venv_or_project"] is True


def test_mechanical_checks_fail_on_bare_dir(tmp_path):
    ws = tmp_path / "bare"
    ws.mkdir()
    checks = _mechanical_checks(ws)
    assert checks == {"venv_or_project": False, "readme_run_instructions": False}


# ── 11. Symlink registration into UA_DEMOS_ROOT ─────────────────────────────

def test_symlink_registration_suffixes_and_rerun_reuse(tmp_path, monkeypatch):
    demos_root = tmp_path / "demos_root"
    monkeypatch.setenv("UA_DEMOS_ROOT", str(demos_root))

    ws1 = tmp_path / "ws1"
    ws1.mkdir()
    result1 = _finalize(ws1)
    assert result1["demo_id"] == "build-an-agent-sdk-demo__demo-1"
    link1 = demos_root / result1["demo_id"]
    assert link1.is_symlink()
    assert link1.resolve() == ws1.resolve()

    # Second distinct workspace, same slug → __demo-2.
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    result2 = _finalize(ws2, mission_id="vp-mission-p6demo2")
    assert result2["demo_id"] == "build-an-agent-sdk-demo__demo-2"
    assert (demos_root / result2["demo_id"]).resolve() == ws2.resolve()

    # Re-run on ws1 → reuses __demo-1 (idempotent).
    rerun = _finalize(ws1)
    assert rerun["demo_id"] == "build-an-agent-sdk-demo__demo-1"
    assert sorted(p.name for p in demos_root.iterdir()) == [
        "build-an-agent-sdk-demo__demo-1",
        "build-an-agent-sdk-demo__demo-2",
    ]


# ── 12. The P5 stamp succeeds after synthesis (pins the no-op fix) ──────────

def test_stamp_succeeds_after_finalize_synthesis(tmp_path, monkeypatch):
    from universal_agent.vp import worker_loop

    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    ws = tmp_path / "ws"
    ws.mkdir()

    # Without finalize the stamp no-ops (test_demo_tutorial_session_link.py
    # pins that). After finalize it must succeed.
    assert _finalize(ws)["ok"] is True
    stamped = worker_loop._stamp_demo_manifest_build_session(
        workspace_dir=str(ws),
        mission_id="vp-mission-x",
        vp_id="vp.coder.primary",
    )
    assert stamped is True
    raw = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert raw["build_session_id"] == "vp-mission-x"
    assert raw["build_vp_id"] == "vp.coder.primary"


# ── 13. The demos walker surfaces the symlinked + stamped workspace ─────────

def test_demos_walker_surfaces_symlinked_finalized_demo(tmp_path, monkeypatch):
    from universal_agent import gateway_server
    from universal_agent.vp import worker_loop

    demos_root = tmp_path / "demos_root"
    monkeypatch.setenv("UA_DEMOS_ROOT", str(demos_root))
    ws = tmp_path / "ws"
    ws.mkdir()

    result = _finalize(ws)
    assert result["ok"] is True
    assert worker_loop._stamp_demo_manifest_build_session(
        workspace_dir=str(ws),
        mission_id="vp-mission-walker",
        vp_id="vp.coder.primary",
    ) is True

    demos = gateway_server._claude_code_intel_demos(
        demos_root=demos_root, vault_root=tmp_path / "vault"
    )
    by_id = {d["demo_id"]: d for d in demos}
    assert result["demo_id"] in by_id
    demo = by_id[result["demo_id"]]
    assert demo["session_id"] == "vp-mission-walker"
    assert "session_id=vp-mission-walker" in demo["session_url"]
    assert demo["endpoint_hit"] == "zai"
