import asyncio

from universal_agent.tools import research_bridge as rb


def _run(coro):
    return asyncio.run(coro)


def test_run_research_phase_wrapper_prefers_explicit_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "session_ws"
    workspace.mkdir()
    captured: dict[str, str] = {}

    async def _fake_phase(query: str, task_name: str, workspace_dir: str | None = None) -> str:
        captured["query"] = query
        captured["task_name"] = task_name
        captured["workspace_dir"] = str(workspace_dir or "")
        return "ok-phase"

    monkeypatch.setattr(rb, "research_phase_core", _fake_phase)

    result = _run(
        rb.run_research_phase_wrapper.handler(
            {
                "query": "q",
                "task_name": "alpha_task",
                "workspace_dir": str(workspace),
            }
        )
    )

    assert captured["workspace_dir"] == str(workspace.resolve())
    assert result["content"][0]["text"] == "ok-phase"


def test_run_research_phase_wrapper_falls_back_to_marker_resolver(monkeypatch, tmp_path):
    workspace = tmp_path / "marker_ws"
    workspace.mkdir()
    captured: dict[str, str] = {}

    async def _fake_phase(query: str, task_name: str, workspace_dir: str | None = None) -> str:
        captured["workspace_dir"] = str(workspace_dir or "")
        return "ok-phase-marker"

    monkeypatch.setattr(rb, "research_phase_core", _fake_phase)
    monkeypatch.setattr(rb, "_ctx_get_workspace", lambda: None)
    monkeypatch.setattr(
        rb,
        "resolve_current_session_workspace",
        lambda repo_root=None: str(workspace),
    )

    result = _run(
        rb.run_research_phase_wrapper.handler(
            {
                "query": "q",
                "task_name": "beta_task",
            }
        )
    )

    assert captured["workspace_dir"] == str(workspace.resolve())
    assert result["content"][0]["text"] == "ok-phase-marker"


def test_run_research_pipeline_wrapper_passes_workspace_hint(monkeypatch, tmp_path):
    workspace = tmp_path / "pipeline_ws"
    workspace.mkdir()
    captured: dict[str, str] = {}

    async def _fake_pipeline(query: str, task_name: str, workspace_dir: str | None = None) -> str:
        captured["workspace_dir"] = str(workspace_dir or "")
        return "ok-pipeline"

    monkeypatch.setattr(rb, "original_pipeline", _fake_pipeline)

    result = _run(
        rb.run_research_pipeline_wrapper.handler(
            {
                "query": "pipeline q",
                "task_name": "gamma_task",
                "workspace_dir": str(workspace),
            }
        )
    )

    assert captured["workspace_dir"] == str(workspace.resolve())
    assert result["content"][0]["text"] == "ok-pipeline"
