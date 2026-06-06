"""Unit tests for viewer.resolver.select_primary_log_rel.

VP SDK missions write a 0-byte ``run.log`` while the real transcript lands in
``transcript.md`` / ``trace.json`` (written by process_turn) in the SAME mission
log dir. A card that links blindly to ``run.log`` therefore opens an empty file.
``select_primary_log_rel`` picks the populated file, preferring run.log (only
when non-empty) → transcript.md → trace.json, and returns "" when none exist so
the caller degrades gracefully.

Regression guard for issue #7 (run.log empty but card links to it).
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.viewer.resolver import (
    mission_log_rel,
    select_primary_log_rel,
)


def _seed_vp_log_dir(
    root: Path,
    vp: str,
    mission_id: str,
    *,
    run_log_bytes: bytes = b"",
    transcript: str | None = None,
    trace: str | None = None,
) -> str:
    """Create a doubly-nested VP mission log dir and return its rel path."""
    d = root / f"vp_{vp}_external" / mission_id / mission_id
    d.mkdir(parents=True)
    # run.log always exists for a VP SDK mission, but is 0 bytes by default.
    (d / "run.log").write_bytes(run_log_bytes)
    if transcript is not None:
        (d / "transcript.md").write_text(transcript, encoding="utf-8")
    if trace is not None:
        (d / "trace.json").write_text(trace, encoding="utf-8")
    return str(d.relative_to(root))


def test_selects_transcript_when_run_log_zero_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    mid = "vp-mission-zerorunlog"
    rel = _seed_vp_log_dir(
        tmp_path,
        "coder_primary",
        mid,
        run_log_bytes=b"",  # 0 bytes — the bug condition
        transcript="# Mission transcript\nUSER: hi\n",
        trace='{"messages": []}',
    )
    assert select_primary_log_rel(rel) == f"{rel}/transcript.md"


def test_selects_trace_when_run_log_empty_and_transcript_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    mid = "vp-mission-traceonly"
    rel = _seed_vp_log_dir(
        tmp_path,
        "general_primary",
        mid,
        run_log_bytes=b"",
        transcript=None,  # no transcript.md
        trace='{"messages": [{"role": "user"}]}',
    )
    assert select_primary_log_rel(rel) == f"{rel}/trace.json"


def test_prefers_run_log_when_populated(tmp_path, monkeypatch):
    # The common (non-VP-SDK) path: run.log has content → keep it.
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    mid = "vp-mission-goodrunlog"
    rel = _seed_vp_log_dir(
        tmp_path,
        "coder_primary",
        mid,
        run_log_bytes=b"[00:00:00] USER: hi\n",
        transcript="# transcript\n",
        trace="{}",
    )
    assert select_primary_log_rel(rel) == f"{rel}/run.log"


def test_returns_empty_when_no_populated_file(tmp_path, monkeypatch):
    # All candidates are 0 bytes / absent → "" so the caller degrades gracefully.
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    mid = "vp-mission-allempty"
    rel = _seed_vp_log_dir(
        tmp_path,
        "coder_primary",
        mid,
        run_log_bytes=b"",
        transcript="",  # 0 bytes
        trace="",  # 0 bytes
    )
    assert select_primary_log_rel(rel) == ""


def test_returns_empty_for_blank_or_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    assert select_primary_log_rel("") == ""
    assert select_primary_log_rel("   ") == ""
    assert select_primary_log_rel(None) == ""  # type: ignore[arg-type]
    # Points at a dir that doesn't exist on disk.
    assert select_primary_log_rel("vp_coder_primary_external/nope/nope") == ""


def test_integrates_with_mission_log_rel(tmp_path, monkeypatch):
    # End-to-end: the dir mission_log_rel finds feeds select_primary_log_rel,
    # which downgrades from the 0-byte run.log to the populated transcript.md.
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    mid = "vp-mission-8692bb058e6d648b25566463"
    _seed_vp_log_dir(
        tmp_path,
        "coder_primary",
        mid,
        run_log_bytes=b"",
        transcript="# real content\n",
        trace="{}",
    )
    log_dir_rel = mission_log_rel(mid)
    assert log_dir_rel == f"vp_coder_primary_external/{mid}/{mid}"
    assert select_primary_log_rel(log_dir_rel) == f"{log_dir_rel}/transcript.md"
