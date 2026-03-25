import asyncio

from universal_agent.tools import research_bridge as rb


def _run(coro):
    return asyncio.run(coro)


def _make_workspace(tmp_path, name: str, *, run_manifest: bool = False):
    workspace = tmp_path / name
    workspace.mkdir()
    (workspace / "work_products").mkdir()
    (workspace / "session_policy.json").write_text("{}", encoding="utf-8")
    if run_manifest:
        (workspace / "run_manifest.json").write_text("{}", encoding="utf-8")
    return workspace


def test_run_research_phase_wrapper_prefers_explicit_workspace(monkeypatch, tmp_path):
    workspace = _make_workspace(tmp_path, "session_explicit_ws")
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
    workspace = _make_workspace(tmp_path, "session_marker_ws")
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
    workspace = _make_workspace(tmp_path, "session_pipeline_ws")
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


def test_run_research_phase_wrapper_ignores_non_session_explicit_workspace(monkeypatch, tmp_path):
    explicit_non_session = tmp_path / "repo_root_like"
    explicit_non_session.mkdir()
    fallback_workspace = _make_workspace(tmp_path, "session_fallback_ws")
    captured: dict[str, str] = {}

    async def _fake_phase(query: str, task_name: str, workspace_dir: str | None = None) -> str:
        captured["workspace_dir"] = str(workspace_dir or "")
        return "ok-fallback"

    monkeypatch.setattr(rb, "research_phase_core", _fake_phase)
    monkeypatch.setattr(
        rb,
        "resolve_current_session_workspace",
        lambda repo_root=None: str(fallback_workspace),
    )

    result = _run(
        rb.run_research_phase_wrapper.handler(
            {
                "query": "q",
                "task_name": "delta_task",
                "workspace_dir": str(explicit_non_session),
            }
        )
    )

    assert captured["workspace_dir"] == str(fallback_workspace.resolve())
    assert result["content"][0]["text"] == "ok-fallback"


def test_run_research_phase_wrapper_accepts_explicit_run_workspace(monkeypatch, tmp_path):
    workspace = _make_workspace(tmp_path, "run_explicit_ws", run_manifest=True)
    captured: dict[str, str] = {}

    async def _fake_phase(query: str, task_name: str, workspace_dir: str | None = None) -> str:
        captured["workspace_dir"] = str(workspace_dir or "")
        return "ok-run"

    monkeypatch.setattr(rb, "research_phase_core", _fake_phase)

    result = _run(
        rb.run_research_phase_wrapper.handler(
            {
                "query": "q",
                "task_name": "epsilon_task",
                "workspace_dir": str(workspace),
            }
        )
    )

    assert captured["workspace_dir"] == str(workspace.resolve())
    assert result["content"][0]["text"] == "ok-run"


def test_is_session_workspace_accepts_run_workspace(tmp_path):
    workspace = _make_workspace(tmp_path, "run_workspace_ws", run_manifest=True)

    assert rb._is_session_workspace(str(workspace)) is True
