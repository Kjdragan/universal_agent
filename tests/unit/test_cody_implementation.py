"""Tests for the Phase 3 Cody implementation helpers (PR 9)."""

from __future__ import annotations

import json
import os
import subprocess as sp
from datetime import datetime, timezone
from pathlib import Path

import pytest

from universal_agent.services.cody_implementation import (
    LEAKY_ANTHROPIC_ENV_VARS,
    BriefingBundle,
    DemoManifest,
    RunResult,
    WorkspaceArtifacts,
    WorkspaceReadiness,
    append_build_note,
    detect_endpoint_from_text,
    list_sources,
    load_briefing,
    probe_versions,
    read_manifest,
    run_in_workspace,
    verify_workspace_ready,
    workspace_for,
    write_manifest,
    write_run_output,
)


# ── WorkspaceArtifacts shape ────────────────────────────────────────────────


def test_workspace_artifacts_paths_resolve_correctly(tmp_path: Path):
    artifacts = workspace_for(tmp_path)
    assert artifacts.workspace_dir == tmp_path.resolve()
    assert artifacts.brief_path == tmp_path.resolve() / "BRIEF.md"
    assert artifacts.acceptance_path == tmp_path.resolve() / "ACCEPTANCE.md"
    assert artifacts.business_relevance_path == tmp_path.resolve() / "business_relevance.md"
    assert artifacts.sources_dir == tmp_path.resolve() / "SOURCES"
    assert artifacts.manifest_path == tmp_path.resolve() / "manifest.json"
    assert artifacts.build_notes_path == tmp_path.resolve() / "BUILD_NOTES.md"
    assert artifacts.run_output_path == tmp_path.resolve() / "run_output.txt"
    assert artifacts.feedback_path == tmp_path.resolve() / "FEEDBACK.md"
    assert artifacts.settings_path == tmp_path.resolve() / ".claude" / "settings.json"


# ── verify_workspace_ready ──────────────────────────────────────────────────


def _make_ready_workspace(tmp_path: Path) -> Path:
    """Helper: produce a workspace that passes readiness."""
    ws = tmp_path / "demo"
    (ws / ".claude").mkdir(parents=True)
    (ws / "SOURCES").mkdir()
    (ws / ".claude" / "settings.json").write_text(
        json.dumps({"effortLevel": "high", "permissions": {"allow": []}}),
        encoding="utf-8",
    )
    (ws / "BRIEF.md").write_text("# Skills\n\nReal feature briefing — Simone has refined this.\n", encoding="utf-8")
    (ws / "ACCEPTANCE.md").write_text(
        "# Acceptance\n\n1. demo MUST do X.\n2. demo MUST output OK.\n",
        encoding="utf-8",
    )
    (ws / "business_relevance.md").write_text("# Business\n\nClient relevance text.\n", encoding="utf-8")
    return ws


def test_verify_workspace_ready_passes_when_complete(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    result = verify_workspace_ready(ws)
    assert result.ok is True
    assert result.reasons == ()
    assert result.iteration == 1


def test_verify_workspace_ready_fails_when_brief_missing(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    (ws / "BRIEF.md").unlink()
    result = verify_workspace_ready(ws)
    assert result.ok is False
    assert any("BRIEF.md missing" in r for r in result.reasons)


def test_verify_workspace_ready_fails_on_unrefined_placeholders(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    (ws / "BRIEF.md").write_text(
        "# Skills\n\n_(Simone: synthesize the body)_\n",
        encoding="utf-8",
    )
    result = verify_workspace_ready(ws)
    assert result.ok is False
    assert any("placeholders" in r for r in result.reasons)


def test_verify_workspace_ready_rejects_polluted_settings(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    (ws / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://api.z.ai"}}),
        encoding="utf-8",
    )
    result = verify_workspace_ready(ws)
    assert result.ok is False
    assert any("pollution markers" in r for r in result.reasons)


def test_verify_workspace_ready_detects_iteration_via_feedback(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    (ws / "FEEDBACK.md").write_text("- Cody: please do X differently.\n", encoding="utf-8")
    # No prior manifest → iteration defaults to 2.
    result = verify_workspace_ready(ws)
    assert result.iteration == 2


def test_verify_workspace_ready_iteration_from_manifest(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    (ws / "FEEDBACK.md").write_text("feedback", encoding="utf-8")
    (ws / "manifest.json").write_text(json.dumps({"iteration": 3, "demo_id": "x", "feature": "y"}), encoding="utf-8")
    result = verify_workspace_ready(ws)
    assert result.iteration == 4


def test_verify_workspace_ready_returns_loud_when_workspace_missing(tmp_path: Path):
    result = verify_workspace_ready(tmp_path / "does_not_exist")
    assert result.ok is False
    assert any("does not exist" in r for r in result.reasons)


# ── load_briefing & list_sources ────────────────────────────────────────────


def test_load_briefing_reads_all_three(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    bundle = load_briefing(ws)
    assert isinstance(bundle, BriefingBundle)
    assert "Skills" in bundle.brief
    assert "demo MUST" in bundle.acceptance
    assert "Client" in bundle.business_relevance
    assert bundle.feedback == ""


def test_load_briefing_includes_feedback_on_iteration(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    (ws / "FEEDBACK.md").write_text("- bullet point of feedback\n", encoding="utf-8")
    bundle = load_briefing(ws)
    assert "bullet point" in bundle.feedback


def test_load_briefing_handles_partial_workspace(tmp_path: Path):
    ws = tmp_path / "incomplete"
    ws.mkdir()
    bundle = load_briefing(ws)
    assert bundle.brief == ""
    assert bundle.acceptance == ""
    assert bundle.business_relevance == ""


def test_list_sources_returns_files_only(tmp_path: Path):
    ws = _make_ready_workspace(tmp_path)
    (ws / "SOURCES" / "doc1.md").write_text("# 1\n", encoding="utf-8")
    (ws / "SOURCES" / "doc2.md").write_text("# 2\n", encoding="utf-8")
    (ws / "SOURCES" / "subdir").mkdir()  # subdir, should NOT be listed
    sources = list_sources(ws)
    names = {p.name for p in sources}
    assert names == {"doc1.md", "doc2.md"}


def test_list_sources_returns_empty_when_no_dir(tmp_path: Path):
    ws = tmp_path / "no_sources"
    ws.mkdir()
    assert list_sources(ws) == []


# ── run_in_workspace ────────────────────────────────────────────────────────


def test_run_in_workspace_executes_and_captures_stdout(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    result = run_in_workspace(
        ws,
        ["python3", "-c", "print('hello from inside workspace')"],
        timeout=15,
    )
    assert isinstance(result, RunResult)
    assert result.ok is True
    assert "hello from inside workspace" in result.stdout
    assert result.cwd == str(ws.resolve())
    assert result.wall_time_seconds >= 0


def test_run_in_workspace_scrubs_leaky_anthropic_env(tmp_path: Path, monkeypatch):
    """ANTHROPIC_AUTH_TOKEN must NOT leak into the subprocess by default."""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should_not_leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://wrong.example/")
    ws = tmp_path / "ws"
    ws.mkdir()
    result = run_in_workspace(
        ws,
        [
            "python3",
            "-c",
            "import os; "
            "print('TOKEN=' + os.environ.get('ANTHROPIC_AUTH_TOKEN', 'unset'));"
            "print('URL=' + os.environ.get('ANTHROPIC_BASE_URL', 'unset'))",
        ],
        timeout=15,
    )
    assert "TOKEN=unset" in result.stdout
    assert "URL=unset" in result.stdout
    assert result.env_scrubbed is True


def test_run_in_workspace_scrub_env_false_passes_through(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "intentional_passthrough")
    ws = tmp_path / "ws"
    ws.mkdir()
    result = run_in_workspace(
        ws,
        ["python3", "-c", "import os; print(os.environ.get('ANTHROPIC_AUTH_TOKEN', 'unset'))"],
        timeout=15,
        scrub_env=False,
    )
    assert "intentional_passthrough" in result.stdout
    assert result.env_scrubbed is False


def test_run_in_workspace_handles_nonzero_exit(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    result = run_in_workspace(ws, ["python3", "-c", "import sys; sys.exit(7)"], timeout=15)
    assert result.return_code == 7
    assert result.ok is False


def test_run_in_workspace_handles_missing_binary(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    result = run_in_workspace(ws, ["this_binary_does_not_exist_anywhere_xyz"], timeout=15)
    assert result.return_code == 127
    assert "binary_not_found" in result.stderr


def test_run_in_workspace_raises_for_missing_workspace(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_in_workspace(tmp_path / "does_not_exist", ["echo", "hi"], timeout=5)


def test_run_in_workspace_respects_timeout(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    result = run_in_workspace(ws, ["python3", "-c", "import time; time.sleep(10)"], timeout=1)
    assert result.return_code == 124
    assert "timeout" in result.stderr


def test_leaky_env_var_list_includes_critical_items():
    """Catch a future regression that drops one of the must-scrub vars."""
    for v in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        assert v in LEAKY_ANTHROPIC_ENV_VARS


# ── detect_endpoint_from_text ───────────────────────────────────────────────


def test_detect_endpoint_anthropic_native_hints():
    assert detect_endpoint_from_text("Connecting to api.anthropic.com") == "anthropic_native"
    assert detect_endpoint_from_text("model: claude-haiku-4-5") == "anthropic_native"
    assert detect_endpoint_from_text("with Claude Max") == "anthropic_native"


def test_detect_endpoint_zai_hints():
    assert detect_endpoint_from_text("Routing through api.z.ai") == "zai"
    assert detect_endpoint_from_text("model: glm-5-turbo") == "zai"


def test_detect_endpoint_zai_wins_over_anthropic_native_when_both_present():
    """If ZAI hints appear at all, the request hit ZAI — that's a failure."""
    text = "claude-haiku-4-5 ... but routed through api.z.ai"
    assert detect_endpoint_from_text(text) == "zai"


def test_detect_endpoint_unknown_when_neutral():
    assert detect_endpoint_from_text("just some output, no telltale strings") == "unknown"
    assert detect_endpoint_from_text("") == "unknown"


# ── DemoManifest read/write ─────────────────────────────────────────────────


def test_write_and_read_manifest_round_trip(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    manifest = DemoManifest(
        demo_id="skills__demo-1",
        feature="skills",
        endpoint_required="anthropic_native",
        endpoint_hit="anthropic_native",
        model_used="claude-haiku-4-5-20251001",
        claude_code_version="2.1.116",
        wall_time_seconds=120.5,
        acceptance_passed=True,
        iteration=1,
        started_at="2026-05-05T12:00:00+00:00",
        finished_at="2026-05-05T12:02:00+00:00",
        notes="first pass",
    )
    write_manifest(ws, manifest)
    loaded = read_manifest(ws)
    assert loaded is not None
    assert loaded.demo_id == "skills__demo-1"
    assert loaded.endpoint_hit == "anthropic_native"
    assert loaded.acceptance_passed is True
    assert loaded.wall_time_seconds == 120.5


def test_read_manifest_returns_none_when_missing(tmp_path: Path):
    assert read_manifest(tmp_path / "ws") is None


def test_manifest_endpoint_match_check():
    m_match = DemoManifest(
        demo_id="x", feature="y", endpoint_required="anthropic_native", endpoint_hit="anthropic_native"
    )
    m_mismatch = DemoManifest(
        demo_id="x", feature="y", endpoint_required="anthropic_native", endpoint_hit="zai"
    )
    m_any = DemoManifest(demo_id="x", feature="y", endpoint_required="any", endpoint_hit="zai")
    assert m_match.endpoint_matches_required is True
    assert m_mismatch.endpoint_matches_required is False
    assert m_any.endpoint_matches_required is True


# ── BUILD_NOTES.md ──────────────────────────────────────────────────────────


def test_append_build_note_creates_file_and_appends(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    target = append_build_note(ws, "Couldn't find SkillRegistry init in docs.", kind="gap")
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "GAP" in text
    assert "SkillRegistry" in text
    # Append a second note.
    append_build_note(ws, "Picked the variant from docs/skills/quickstart.md.", kind="decision")
    text = target.read_text(encoding="utf-8")
    assert "DECISION" in text
    assert "quickstart" in text
    # Two distinct entries.
    assert text.count("##") >= 2


def test_append_build_note_tolerates_arbitrary_kind(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    append_build_note(ws, "note body", kind="custom_kind")
    text = (ws / "BUILD_NOTES.md").read_text(encoding="utf-8")
    assert "CUSTOM_KIND" in text


# ── write_run_output ────────────────────────────────────────────────────────


def test_write_run_output_writes_text(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    target = write_run_output(ws, "captured stdout content")
    assert target.read_text(encoding="utf-8") == "captured stdout content"


# ── probe_versions ──────────────────────────────────────────────────────────


def test_probe_versions_returns_dict():
    """Best-effort — must not raise even when binaries/packages are missing."""
    versions = probe_versions()
    assert isinstance(versions, dict)
    # Don't assert specific keys — depends on local install state.
