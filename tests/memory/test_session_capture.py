from __future__ import annotations

from pathlib import Path

from universal_agent.memory.orchestrator import get_memory_orchestrator


def test_capture_session_rollover_from_transcript(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared_memory"
    workspace = tmp_path / "session_workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    transcript = workspace / "transcript.md"
    transcript.write_text(
        "### User Request\n> Build a rollout checklist.\n\n### Assistant Response\nCreated one.\n",
        encoding="utf-8",
    )
    run_log = workspace / "run.log"
    run_log.write_text("fallback run log", encoding="utf-8")

    broker = get_memory_orchestrator(workspace_dir=str(shared_root))
    result = broker.capture_session_rollover(
        session_id="session_demo_123",
        trigger="test_capture",
        transcript_path=str(transcript),
        run_log_path=str(run_log),
        summary="Rollout checklist session",
    )

    assert result["captured"] is True
    rel_path = str(result["path"])
    target = shared_root / rel_path
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "Session Capture" in content
    assert "session_demo_123" in content
    assert "Build a rollout checklist" in content

    duplicate = broker.capture_session_rollover(
        session_id="session_demo_123",
        trigger="test_capture",
        transcript_path=str(transcript),
        run_log_path=str(run_log),
        summary="Rollout checklist session",
    )
    assert duplicate["captured"] is False
    assert duplicate["reason"] == "duplicate"


def test_capture_session_rollover_uses_runlog_fallback(tmp_path: Path) -> None:
    shared_root = tmp_path / "shared_memory"
    workspace = tmp_path / "session_workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    run_log = workspace / "run.log"
    run_log.write_text("run log tail about deployment incident triage", encoding="utf-8")

    broker = get_memory_orchestrator(workspace_dir=str(shared_root))
    result = broker.capture_session_rollover(
        session_id="session_demo_456",
        trigger="fallback_capture",
        transcript_path=str(workspace / "transcript.md"),
        run_log_path=str(run_log),
        summary="Deployment incident triage",
    )

    assert result["captured"] is True
    assert result["source"] == "run_log"
